import importlib.machinery
import importlib.util
import json
import tempfile
import time
import unittest
from pathlib import Path


class TelemetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        loader = importlib.machinery.SourceFileLoader("spawnwp_telemetry", str(Path(__file__).parents[1] / "telemetry.py"))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        self.module = importlib.util.module_from_spec(spec)
        loader.exec_module(self.module)
        self.module.ROOT = root / "telemetry"
        self.module.CONSENT = self.module.ROOT / "consent.json"
        self.module.PENDING = self.module.ROOT / "pending.json"
        self.module.VERSION_FILE = root / "VERSION"
        self.module.FEATURES_FILE = root / "features.json"
        self.module.ENVIRONMENTS_ROOT = root / "srv"
        self.module.ROOT.mkdir()
        self.module.ENVIRONMENTS_ROOT.mkdir()
        self.module.VERSION_FILE.write_text("0.2.0\n")
        self.module.FEATURES_FILE.write_text('{"port_knocking":true,"telemetry":true}\n')
        (self.module.ROOT / "installation-id").write_text("random-id\n")

    def tearDown(self):
        self.temp.cleanup()

    def test_payload_allowlist_and_expiry(self):
        self.module.CONSENT.write_text(json.dumps({"enabled": True, "expires_at": int(time.time()) + 60}))
        data = self.module.payload()
        self.assertEqual(set(data), {"installation_id", "event", "timestamp", "spawnwp_version",
                                    "os_family", "os_version", "architecture", "features", "counters"})
        serialized = json.dumps(data).lower()
        for prohibited in ("domain", "email", "username", "site_name", "password", "command"):
            self.assertNotIn(prohibited, serialized)
        self.module.CONSENT.write_text(json.dumps({"enabled": True, "expires_at": int(time.time()) - 1}))
        self.assertIsNone(self.module.payload())
        self.assertFalse((self.module.ROOT / "installation-id").exists())


if __name__ == "__main__":
    unittest.main()
