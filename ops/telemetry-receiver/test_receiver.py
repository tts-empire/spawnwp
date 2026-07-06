import importlib.util
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

path = Path(__file__).with_name("app.py")
spec = importlib.util.spec_from_file_location("telemetry_receiver", path)
receiver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(receiver)


class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        receiver.DB_PATH = root / "telemetry.sqlite3"
        receiver.KEY_PATH = root / "key"
        receiver.KEY_PATH.write_bytes(b"x" * 32)
        self.client = TestClient(receiver.app)

    def tearDown(self): self.temp.cleanup()

    def payload(self, identifier=None):
        return {"installation_id": str(identifier or uuid4()), "event": "heartbeat",
                "timestamp": datetime.now(timezone.utc).isoformat(), "spawnwp_version": "0.3.1",
                "os_family": "Linux", "os_version": "6.8", "architecture": "aarch64",
                "features": {"telemetry": True}, "counters": {"environments_current": 2}}

    def test_upsert_and_disable(self):
        identifier = uuid4()
        self.assertEqual(202, self.client.post("/api/v1/telemetry", json=self.payload(identifier)).status_code)
        self.assertEqual(202, self.client.post("/api/v1/telemetry", json=self.payload(identifier)).status_code)
        with receiver.connection() as db:
            self.assertEqual((1, 2), db.execute("SELECT count(*),heartbeat_count FROM installations").fetchone())
        with receiver.connection() as db:
            self.assertEqual(2, db.execute("SELECT count(*) FROM heartbeats_raw").fetchone()[0])
        disable = {"installation_id": str(identifier), "event": "disable", "timestamp": datetime.now(timezone.utc).isoformat()}
        self.assertEqual(202, self.client.post("/api/v1/telemetry", json=disable).status_code)
        with receiver.connection() as db:
            self.assertEqual(0, db.execute("SELECT count(*) FROM installations").fetchone()[0])
            self.assertEqual(0, db.execute("SELECT count(*) FROM heartbeats_raw").fetchone()[0])

    def test_raw_archive_keeps_dated_beats_and_purges_by_age(self):
        import json as _json, time as _time
        payload = self.payload()
        payload["metrics"] = {"create_warm_count": 3, "create_warm_seconds_sum": 90}
        payload["hardware"] = {"cpu_count": 8, "ram_gb": 16, "disk_total_gb": 200,
                               "disk_free_gb": 80, "docker_images_gb": 6,
                               "build_cache_gb": 1, "php_versions": 2}
        self.assertEqual(202, self.client.post("/api/v1/telemetry", json=payload).status_code)
        with receiver.connection() as db:
            ts, metrics_json, hardware_json = db.execute(
                "SELECT ts,metrics_json,hardware_json FROM heartbeats_raw").fetchone()
            self.assertEqual(16, _json.loads(hardware_json)["ram_gb"])
            self.assertEqual(3, _json.loads(metrics_json)["create_warm_count"])
            # Age one raw row past the retention window, then confirm purge drops it.
            db.execute("UPDATE heartbeats_raw SET ts = ?", (ts - receiver.RETENTION_SECONDS - 1,))
            receiver.purge(db, now=ts)
            self.assertEqual(0, db.execute("SELECT count(*) FROM heartbeats_raw").fetchone()[0])

    def test_rejects_extra_fields_and_old_timestamps(self):
        payload = self.payload(); payload["domain"] = "example.com"
        self.assertEqual(422, self.client.post("/api/v1/telemetry", json=payload).status_code)
        payload = self.payload(); payload["timestamp"] = "2020-01-01T00:00:00Z"
        self.assertEqual(422, self.client.post("/api/v1/telemetry", json=payload).status_code)

    def test_purge_removes_only_inactive_records(self):
        with receiver.connection() as db:
            db.execute("INSERT INTO installations VALUES ('old',1,1,'0.1.0','Linux','old','x86_64','{}',0,1,NULL,NULL)")
            db.execute("INSERT INTO installations VALUES ('new',1,?, '0.3.1','Linux','new','aarch64','{}',1,1,NULL,NULL)",
                       (int(__import__('time').time()),))
            self.assertEqual(1, receiver.purge(db))
            self.assertEqual(['new'], [row[0] for row in db.execute("SELECT installation_hash FROM installations")])

    def test_v2_payload_without_extended_fields_still_accepted(self):
        self.assertEqual(202, self.client.post("/api/v1/telemetry", json=self.payload()).status_code)
        with receiver.connection() as db:
            self.assertEqual((None, None), db.execute(
                "SELECT metrics_json,hardware_json FROM installations").fetchone())

    def test_v3_metrics_and_hardware_stored(self):
        payload = self.payload()
        payload["metrics"] = {"creates_total": 12, "create_warm_count": 10,
                              "create_warm_seconds_sum": 320, "create_warm_seconds_max": 45}
        payload["hardware"] = {"cpu_count": 4, "ram_gb": 8, "disk_total_gb": 100,
                               "disk_free_gb": 40, "docker_images_gb": 6,
                               "build_cache_gb": 1, "php_versions": 2}
        self.assertEqual(202, self.client.post("/api/v1/telemetry", json=payload).status_code)
        import json as _json
        with receiver.connection() as db:
            metrics_json, hardware_json = db.execute(
                "SELECT metrics_json,hardware_json FROM installations").fetchone()
        self.assertEqual(12, _json.loads(metrics_json)["creates_total"])
        self.assertEqual(8, _json.loads(hardware_json)["ram_gb"])

    def test_rejects_invalid_metrics_and_hardware(self):
        payload = self.payload(); payload["metrics"] = {"site_domains": 1}
        self.assertEqual(422, self.client.post("/api/v1/telemetry", json=payload).status_code)
        payload = self.payload(); payload["metrics"] = {"creates_total": 10**10}
        self.assertEqual(422, self.client.post("/api/v1/telemetry", json=payload).status_code)
        payload = self.payload()
        payload["hardware"] = {"cpu_count": 4, "ram_gb": 8, "disk_total_gb": 100,
                               "disk_free_gb": 40, "docker_images_gb": 6,
                               "build_cache_gb": 1, "php_versions": 2, "hostname": "leak"}
        self.assertEqual(422, self.client.post("/api/v1/telemetry", json=payload).status_code)

    def test_alter_table_migrates_pre_0316_database(self):
        import sqlite3 as _sqlite3
        receiver.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _sqlite3.connect(receiver.DB_PATH) as db:
            db.execute("""CREATE TABLE installations (
                installation_hash TEXT PRIMARY KEY, first_seen INTEGER NOT NULL,
                last_seen INTEGER NOT NULL, spawnwp_version TEXT NOT NULL,
                os_family TEXT NOT NULL, os_version TEXT NOT NULL,
                architecture TEXT NOT NULL, features_json TEXT NOT NULL,
                environments_current INTEGER NOT NULL, heartbeat_count INTEGER NOT NULL)""")
            db.execute("INSERT INTO installations VALUES ('kept',1,?,'0.3.15','Linux','6.8','x86_64','{}',1,1)",
                       (int(__import__('time').time()),))
        self.assertEqual(202, self.client.post("/api/v1/telemetry", json=self.payload()).status_code)
        with receiver.connection() as db:
            rows = db.execute("SELECT installation_hash,metrics_json FROM installations ORDER BY first_seen").fetchall()
        self.assertEqual(2, len(rows))
        self.assertEqual(("kept", None), rows[0])


if __name__ == "__main__": unittest.main()
