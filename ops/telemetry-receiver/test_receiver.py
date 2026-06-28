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
        disable = {"installation_id": str(identifier), "event": "disable", "timestamp": datetime.now(timezone.utc).isoformat()}
        self.assertEqual(202, self.client.post("/api/v1/telemetry", json=disable).status_code)
        with receiver.connection() as db: self.assertEqual(0, db.execute("SELECT count(*) FROM installations").fetchone()[0])

    def test_rejects_extra_fields_and_old_timestamps(self):
        payload = self.payload(); payload["domain"] = "example.com"
        self.assertEqual(422, self.client.post("/api/v1/telemetry", json=payload).status_code)
        payload = self.payload(); payload["timestamp"] = "2020-01-01T00:00:00Z"
        self.assertEqual(422, self.client.post("/api/v1/telemetry", json=payload).status_code)

    def test_purge_removes_only_inactive_records(self):
        with receiver.connection() as db:
            db.execute("INSERT INTO installations VALUES ('old',1,1,'0.1.0','Linux','old','x86_64','{}',0,1)")
            db.execute("INSERT INTO installations VALUES ('new',1,?, '0.3.1','Linux','new','aarch64','{}',1,1)",
                       (int(__import__('time').time()),))
            self.assertEqual(1, receiver.purge(db))
            self.assertEqual(['new'], [row[0] for row in db.execute("SELECT installation_hash FROM installations")])


if __name__ == "__main__": unittest.main()
