import importlib.machinery
import importlib.util
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


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
        self.module.METRICS_FILE = root / "metrics.json"
        self.module.ROOT.mkdir()
        self.module.ENVIRONMENTS_ROOT.mkdir()
        self.module.VERSION_FILE.write_text("0.2.0\n")
        self.module.FEATURES_FILE.write_text('{"telemetry":true}\n')
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
        with patch.object(self.module, "post", return_value=True):
            self.assertIsNone(self.module.payload())
        self.assertFalse((self.module.ROOT / "installation-id").exists())

    def test_v2_consent_keeps_minimal_payload(self):
        self.module.CONSENT.write_text(json.dumps({"enabled": True, "notice_version": "2",
                                                   "expires_at": int(time.time()) + 60}))
        self.module.METRICS_FILE.write_text('{"creates_total": 5}')
        data = self.module.payload()
        self.assertNotIn("metrics", data)
        self.assertNotIn("hardware", data)

    def test_v3_consent_adds_whitelisted_metrics_and_hardware(self):
        self.module.CONSENT.write_text(json.dumps({"enabled": True, "notice_version": "3",
                                                   "expires_at": int(time.time()) + 60}))
        self.module.METRICS_FILE.write_text(json.dumps({
            "creates_total": 5, "create_warm_seconds_sum": 160,
            "site_domain": 1, "creates_failed": -2, "php_switches": "boom"}))
        hardware = {"cpu_count": 4, "ram_gb": 8, "disk_total_gb": 100, "disk_free_gb": 40,
                    "docker_images_gb": 6, "build_cache_gb": 1, "php_versions": 2}
        with patch.object(self.module, "collect_hardware", return_value=hardware):
            data = self.module.payload()
        # Only whitelisted, non-negative integer counters survive.
        self.assertEqual({"creates_total": 5, "create_warm_seconds_sum": 160}, data["metrics"])
        self.assertEqual(hardware, data["hardware"])

    def test_enable_writes_notice_v3(self):
        with patch.object(self.module, "post", return_value=True):
            self.module.enable()
        self.assertEqual("3", json.loads(self.module.CONSENT.read_text())["notice_version"])

    def test_enable_and_disable_manage_consent_identifier_and_features(self):
        old_identifier = (self.module.ROOT / "installation-id").read_text()
        with patch.object(self.module, "post", return_value=True):
            self.module.enable()
        consent = json.loads(self.module.CONSENT.read_text())
        self.assertTrue(consent["enabled"])
        self.assertGreater(consent["expires_at"], int(time.time()) + 89 * 24 * 60 * 60)
        self.assertNotEqual(old_identifier, (self.module.ROOT / "installation-id").read_text())
        self.assertTrue(json.loads(self.module.FEATURES_FILE.read_text())["telemetry"])
        with patch.object(self.module, "post", return_value=True) as send:
            self.module.disable()
        self.assertEqual("disable", send.call_args.args[0]["event"])
        self.assertFalse(self.module.CONSENT.exists())
        self.assertFalse(json.loads(self.module.FEATURES_FILE.read_text())["telemetry"])


if __name__ == "__main__":
    unittest.main()
