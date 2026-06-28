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
    rows = db.execute("SELECT spawnwp_version,os_family,architecture,features_json,environments_current FROM installations").fetchall()
print(f"Active installations (seen within 90 days): {len(rows)}")
for label, index in (("Versions", 0), ("Operating systems", 1), ("Architectures", 2)):
    print(f"\n{label}:")
    for value, count in Counter(row[index] for row in rows).most_common():
        print(f"  {value}: {count}")
features = Counter(key for row in rows for key, enabled in json.loads(row[3]).items() if enabled)
print("\nEnabled features:")
for value, count in features.most_common(): print(f"  {value}: {count}")
print(f"\nCurrent environments reported: {sum(row[4] for row in rows)}")
