#!/usr/bin/env python3
"""Root-only local summary for the SpawnWP telemetry receiver.

No arguments  -> print the plaintext aggregate summary (unchanged format).
--json        -> emit the same aggregates as JSON (used by email_report.py).
"""
import argparse
import json
import os
import sqlite3
from collections import Counter
from pathlib import Path

DB = Path(os.environ.get("SPAWNWP_TELEMETRY_DB", "/var/lib/spawnwp-telemetry-receiver/telemetry.sqlite3"))

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


def collect(db_path: Path = DB) -> dict:
    """Read-only aggregate snapshot of the telemetry database as structured data."""
    if not db_path.is_file():
        raise SystemExit("Telemetry database does not exist")
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(installations)")}
        extended = {"metrics_json", "hardware_json"} <= columns
        select = "SELECT spawnwp_version,os_family,architecture,features_json,environments_current"
        select += ",metrics_json,hardware_json" if extended else ",NULL,NULL"
        rows = db.execute(select + " FROM installations").fetchall()

    report: dict = {
        "installations": len(rows),
        "versions": Counter(r[0] for r in rows).most_common(),
        "operating_systems": Counter(r[1] for r in rows).most_common(),
        "architectures": Counter(r[2] for r in rows).most_common(),
        "features": Counter(
            key for r in rows for key, enabled in json.loads(r[3]).items() if enabled
        ).most_common(),
        "environments_current": sum(r[4] for r in rows),
    }

    metrics = [json.loads(r[5]) for r in rows if r[5]]
    hardware = [json.loads(r[6]) for r in rows if r[6]]
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
    lines = [f"Active installations (seen within 90 days): {report['installations']}"]
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
