#!/usr/bin/env python3
"""Root-only local summary for the SpawnWP telemetry receiver."""
import json
import os
import sqlite3
from collections import Counter
from pathlib import Path

DB = Path(os.environ.get("SPAWNWP_TELEMETRY_DB", "/var/lib/spawnwp-telemetry-receiver/telemetry.sqlite3"))
if not DB.is_file():
    raise SystemExit("Telemetry database does not exist")
with sqlite3.connect(f"file:{DB}?mode=ro", uri=True) as db:
    columns = {row[1] for row in db.execute("PRAGMA table_info(installations)")}
    extended = {"metrics_json", "hardware_json"} <= columns
    select = "SELECT spawnwp_version,os_family,architecture,features_json,environments_current"
    select += ",metrics_json,hardware_json" if extended else ",NULL,NULL"
    rows = db.execute(select + " FROM installations").fetchall()
print(f"Active installations (seen within 90 days): {len(rows)}")
for label, index in (("Versions", 0), ("Operating systems", 1), ("Architectures", 2)):
    print(f"\n{label}:")
    for value, count in Counter(row[index] for row in rows).most_common():
        print(f"  {value}: {count}")
features = Counter(key for row in rows for key, enabled in json.loads(row[3]).items() if enabled)
print("\nEnabled features:")
for value, count in features.most_common(): print(f"  {value}: {count}")
print(f"\nCurrent environments reported: {sum(row[4] for row in rows)}")

# ── Extended aggregates (notice-v3 consents only) ──────────────────────────────
metrics = [json.loads(row[5]) for row in rows if row[5]]
hardware = [json.loads(row[6]) for row in rows if row[6]]
print(f"\nInstallations reporting extended metrics: {len(metrics)}")


def metric_sum(key):
    return sum(m.get(key, 0) for m in metrics)


if metrics:
    print("\nCreate performance (fleet-wide, since each install's first v3 heartbeat):")
    for mode in ("warm", "cold"):
        count = metric_sum(f"create_{mode}_count")
        if count:
            avg = metric_sum(f"create_{mode}_seconds_sum") / count
            worst = max(m.get(f"create_{mode}_seconds_max", 0) for m in metrics)
            print(f"  {mode}: {count} creates, avg {avg:.0f}s, worst {worst}s")
    total, failed = metric_sum("creates_total"), metric_sum("creates_failed")
    if total or failed:
        rate = failed / (total + failed) * 100 if (total + failed) else 0.0
        print(f"  outcomes: {total} succeeded, {failed} failed ({rate:.1f}% failure rate), "
              f"{metric_sum('healthcheck_timeouts')} healthcheck timeouts")
    print("\nFeature usage (fleet totals):")
    labels = (("blueprint_clean", "blueprint: clean"), ("blueprint_demo", "blueprint: demo"),
              ("blueprint_development", "blueprint: development"), ("blueprint_custom", "blueprint: custom"),
              ("sites_temporary_created", "temporary sites created"),
              ("sites_expired_auto", "sites auto-expired"),
              ("php_settings_customized", "creates with custom PHP settings"),
              ("destroys_total", "manual destroys"), ("php_switches", "php switches"),
              ("image_refreshes", "image refreshes"), ("image_deletes", "image deletes"))
    for key, label in labels:
        value = metric_sum(key)
        if value:
            print(f"  {label}: {value}")

if hardware:
    def bucket(value, edges):
        for edge in edges:
            if value < edge:
                return f"<{edge}"
        return f">={edges[-1]}"

    print("\nFleet hardware (rounded):")
    for label, key, edges in (("RAM GB", "ram_gb", (2, 4, 8, 16)),
                              ("CPU count", "cpu_count", (2, 4, 8)),
                              ("Disk free GB", "disk_free_gb", (5, 10, 25, 50))):
        print(f"  {label}: " + ", ".join(f"{name}: {count}" for name, count in
              Counter(bucket(h.get(key, 0), edges) for h in hardware).most_common()))
    avg_images = sum(h.get("docker_images_gb", 0) for h in hardware) / len(hardware)
    avg_cache = sum(h.get("build_cache_gb", 0) for h in hardware) / len(hardware)
    print(f"  Docker footprint: avg {avg_images:.1f} GB images, {avg_cache:.1f} GB build cache")
    print("  PHP versions per host: " + ", ".join(f"{name}: {count}" for name, count in
          Counter(h.get("php_versions", 0) for h in hardware).most_common()))
