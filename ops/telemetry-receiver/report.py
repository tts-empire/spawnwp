#!/usr/bin/env python3
"""Root-only local summary for the SpawnWP telemetry receiver.

No arguments  -> print the plaintext aggregate summary (unchanged format).
--json        -> emit the same aggregates as JSON (used by email_report.py).
"""
import argparse
import json
import os
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

DB = Path(os.environ.get("SPAWNWP_TELEMETRY_DB", "/var/lib/spawnwp-telemetry-receiver/telemetry.sqlite3"))

# A genuine installation keeps its pseudonymous ID and reports weekly. A dense
# burst of one-shot IDs with an otherwise identical host fingerprint is therefore
# much more likely to be automation hitting the public endpoint than a real fleet.
# Keep the thresholds deliberately conservative: release-day traffic should not
# be hidden merely because several users run the same Linux distribution.
ANOMALY_MIN_COHORT = 20
ANOMALY_WINDOW_SECONDS = 6 * 60 * 60
CONFIRMATION_MIN_GAP_SECONDS = 24 * 60 * 60

# Fleet feature-usage counters, in display order. Keep in sync with the METRIC_KEYS
# whitelist in app.py and the cockpit's telemetry sender.
FEATURE_LABELS = (
    ("blueprint_clean", "blueprint: clean"),
    ("blueprint_demo", "blueprint: demo"),
    ("blueprint_development", "blueprint: development"),
    ("blueprint_custom", "blueprint: custom"),
    ("blueprint_captures", "blueprints captured"),
    ("wp_cli_commands", "WP-CLI commands run"),
    ("sites_temporary_created", "temporary sites created"),
    ("sites_expired_auto", "sites auto-expired"),
    ("php_settings_customized", "creates with custom PHP settings"),
    ("destroys_total", "manual destroys"),
    ("php_switches", "php switches"),
    ("image_refreshes", "image refreshes"),
    ("image_deletes", "image deletes"),
)

HARDWARE_BUCKETS = (
    ("RAM GB", "ram_gb", (2, 4, 8, 16)),
    ("CPU count", "cpu_count", (2, 4, 8)),
    ("Disk free GB", "disk_free_gb", (5, 10, 25, 50)),
)


def _bucket(value, edges):
    for edge in edges:
        if value < edge:
            return f"<{edge}"
    return f">={edges[-1]}"


def _utc(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _classify(rows):
    """Return trusted rows, confidence counts and suspicious one-shot cohorts."""
    groups = defaultdict(list)
    for index, row in enumerate(rows):
        if row["heartbeat_count"] != 1:
            continue
        fingerprint = (
            row["spawnwp_version"], row["os_family"], row["os_version"],
            row["architecture"], row["features_json"],
        )
        groups[fingerprint].append((row["first_seen"], index))

    anomalous_indexes = set()
    for members in groups.values():
        members.sort()
        left = 0
        for right, (timestamp, _) in enumerate(members):
            while timestamp - members[left][0] > ANOMALY_WINDOW_SECONDS:
                left += 1
            if right - left + 1 >= ANOMALY_MIN_COHORT:
                anomalous_indexes.update(index for _, index in members[left:right + 1])

    cohorts = []
    anomalous_by_fingerprint = defaultdict(list)
    for index in sorted(anomalous_indexes):
        row = rows[index]
        fingerprint = (
            row["spawnwp_version"], row["os_family"], row["os_version"],
            row["architecture"], row["features_json"],
        )
        anomalous_by_fingerprint[fingerprint].append(row)
    for fingerprint, members in anomalous_by_fingerprint.items():
        version, os_family, os_version, architecture, _ = fingerprint
        cohorts.append({
            "count": len(members),
            "spawnwp_version": version,
            "system": f"{os_family} {os_version} / {architecture}",
            "first_seen": _utc(min(row["first_seen"] for row in members)),
            "last_seen": _utc(max(row["first_seen"] for row in members)),
            "reason": (f"{ANOMALY_MIN_COHORT}+ one-shot IDs with an identical host "
                       f"fingerprint within {ANOMALY_WINDOW_SECONDS // 3600} hours"),
        })
    cohorts.sort(key=lambda cohort: (-cohort["count"], cohort["first_seen"]))

    trusted = [row for index, row in enumerate(rows) if index not in anomalous_indexes]
    confirmed = [
        row for row in trusted
        if row["heartbeat_count"] >= 2
        and row["last_seen"] - row["first_seen"] >= CONFIRMATION_MIN_GAP_SECONDS
    ]
    return trusted, confirmed, cohorts


def collect(db_path: Path = DB) -> dict:
    """Read-only aggregate snapshot of the telemetry database as structured data."""
    if not db_path.is_file():
        raise SystemExit("Telemetry database does not exist")
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        columns = {row[1] for row in db.execute("PRAGMA table_info(installations)")}
        extended = {"metrics_json", "hardware_json"} <= columns
        select = ("SELECT first_seen,last_seen,heartbeat_count,spawnwp_version,os_family,"
                  "os_version,architecture,features_json,environments_current")
        select += (",metrics_json,hardware_json" if extended
                   else ",NULL AS metrics_json,NULL AS hardware_json")
        rows = db.execute(select + " FROM installations").fetchall()

    observed_installations = len(rows)
    rows, confirmed, anomalous_cohorts = _classify(rows)
    anomalous_installations = sum(cohort["count"] for cohort in anomalous_cohorts)

    report: dict = {
        "observed_installations": observed_installations,
        "installations": len(rows),
        "confirmed_installations": len(confirmed),
        "provisional_installations": len(rows) - len(confirmed),
        "anomalous_installations": anomalous_installations,
        "anomalous_cohorts": anomalous_cohorts,
        "versions": Counter(r["spawnwp_version"] for r in rows).most_common(),
        "operating_systems": Counter(r["os_family"] for r in rows).most_common(),
        "architectures": Counter(r["architecture"] for r in rows).most_common(),
        "features": Counter(
            key for r in rows for key, enabled in json.loads(r["features_json"]).items() if enabled
        ).most_common(),
        "environments_current": sum(r["environments_current"] for r in rows),
    }

    metrics = [json.loads(r["metrics_json"]) for r in rows if r["metrics_json"]]
    hardware = [json.loads(r["hardware_json"]) for r in rows if r["hardware_json"]]
    report["metrics_installations"] = len(metrics)

    def metric_sum(key):
        return sum(m.get(key, 0) for m in metrics)

    performance = {}
    if metrics:
        creates = {}
        for mode in ("warm", "cold"):
            count = metric_sum(f"create_{mode}_count")
            if count:
                creates[mode] = {
                    "count": count,
                    "avg_seconds": metric_sum(f"create_{mode}_seconds_sum") / count,
                    "worst_seconds": max(m.get(f"create_{mode}_seconds_max", 0) for m in metrics),
                }
        total, failed = metric_sum("creates_total"), metric_sum("creates_failed")
        outcomes = {}
        if total or failed:
            outcomes = {
                "succeeded": total,
                "failed": failed,
                "failure_rate": (failed / (total + failed) * 100) if (total + failed) else 0.0,
                "healthcheck_timeouts": metric_sum("healthcheck_timeouts"),
            }
        performance = {"creates": creates, "outcomes": outcomes}
    report["performance"] = performance

    report["feature_usage"] = [
        {"key": key, "label": label, "value": metric_sum(key)}
        for key, label in FEATURE_LABELS
        if metric_sum(key)
    ]

    fleet = {}
    if hardware:
        fleet["buckets"] = {
            label: Counter(_bucket(h.get(key, 0), edges) for h in hardware).most_common()
            for label, key, edges in HARDWARE_BUCKETS
        }
        fleet["docker_images_gb"] = sum(h.get("docker_images_gb", 0) for h in hardware) / len(hardware)
        fleet["build_cache_gb"] = sum(h.get("build_cache_gb", 0) for h in hardware) / len(hardware)
        fleet["php_versions_per_host"] = Counter(h.get("php_versions", 0) for h in hardware).most_common()
    report["hardware"] = fleet
    return report


def render_text(report: dict) -> str:
    lines = [
        f"Observed IDs (seen within 90 days): {report['observed_installations']}",
        f"Credible active installations: {report['installations']}",
        f"  confirmed (2+ reports at least 24h apart): {report['confirmed_installations']}",
        f"  provisional: {report['provisional_installations']}",
        f"Suspected anomalous IDs excluded: {report['anomalous_installations']}",
    ]
    if report["anomalous_cohorts"]:
        lines.append("\nSuspected anomalous cohorts:")
        for cohort in report["anomalous_cohorts"]:
            lines.append(
                f"  {cohort['count']} IDs: SpawnWP {cohort['spawnwp_version']}, "
                f"{cohort['system']}, {cohort['first_seen']} to {cohort['last_seen']}"
            )
            lines.append(f"    reason: {cohort['reason']}")
    for label, key in (("Versions", "versions"), ("Operating systems", "operating_systems"),
                       ("Architectures", "architectures")):
        lines.append(f"\n{label}:")
        for value, count in report[key]:
            lines.append(f"  {value}: {count}")
    lines.append("\nEnabled features:")
    for value, count in report["features"]:
        lines.append(f"  {value}: {count}")
    lines.append(f"\nCurrent environments reported: {report['environments_current']}")
    lines.append(f"\nInstallations reporting extended metrics: {report['metrics_installations']}")

    perf = report["performance"]
    if perf:
        lines.append("\nCreate performance (fleet-wide, since each install's first v3 heartbeat):")
        for mode, data in perf.get("creates", {}).items():
            lines.append(f"  {mode}: {data['count']} creates, avg {data['avg_seconds']:.0f}s, "
                         f"worst {data['worst_seconds']}s")
        outcomes = perf.get("outcomes") or {}
        if outcomes:
            lines.append(f"  outcomes: {outcomes['succeeded']} succeeded, {outcomes['failed']} failed "
                         f"({outcomes['failure_rate']:.1f}% failure rate), "
                         f"{outcomes['healthcheck_timeouts']} healthcheck timeouts")
    if report["feature_usage"]:
        lines.append("\nFeature usage (fleet totals):")
        for item in report["feature_usage"]:
            lines.append(f"  {item['label']}: {item['value']}")

    fleet = report["hardware"]
    if fleet:
        lines.append("\nFleet hardware (rounded):")
        for label, counts in fleet["buckets"].items():
            lines.append(f"  {label}: " + ", ".join(f"{name}: {count}" for name, count in counts))
        lines.append(f"  Docker footprint: avg {fleet['docker_images_gb']:.1f} GB images, "
                     f"{fleet['build_cache_gb']:.1f} GB build cache")
        lines.append("  PHP versions per host: " + ", ".join(
            f"{name}: {count}" for name, count in fleet["php_versions_per_host"]))
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="emit structured JSON")
    args = parser.parse_args()
    report = collect()
    if args.json:
        print(json.dumps(report))
    else:
        print(render_text(report))


if __name__ == "__main__":
    main()
