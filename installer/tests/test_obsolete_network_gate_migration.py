import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


path = Path(__file__).parents[1] / "migrations/remove-obsolete-network-gate.py"
spec = importlib.util.spec_from_file_location("obsolete_network_gate_migration", path)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class ObsoleteNetworkGateMigrationTests(unittest.TestCase):
    def test_removes_nginx_include_and_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nginx = root / "spawnwp"
            config = root / "config.env"
            features = root / "features.json"
            report = root / "report.txt"
            nginx.write_text("server {\n    include /etc/nginx/cockpit-allowed.conf;\n}\n")
            config.write_text("DOMAIN=dev.example.com\nENABLE_PORT_KNOCKING=1\n")
            features.write_text('{"port_knocking":true,"telemetry":false}\n')
            report.write_text(
                "Credentials\n\nPort-knocking: enabled\n  open sequence: 1 2 3\n\n"
                "This root-only report is stored at:\n  /root/report\n"
            )
            old = (migration.NGINX_CONF, migration.CONFIG_ENV, migration.FEATURES, migration.REPORT)
            migration.NGINX_CONF, migration.CONFIG_ENV = nginx, config
            migration.FEATURES, migration.REPORT = features, report
            try:
                with mock.patch.object(migration, "run"):
                    migration.remove_nginx_gate()
                migration.clean_metadata()
            finally:
                migration.NGINX_CONF, migration.CONFIG_ENV, migration.FEATURES, migration.REPORT = old
            self.assertNotIn("cockpit-allowed", nginx.read_text())
            self.assertNotIn("ENABLE_PORT_KNOCKING", config.read_text())
            self.assertEqual({"telemetry": False}, json.loads(features.read_text()))
            self.assertNotIn("Port-knocking", report.read_text())


if __name__ == "__main__":
    unittest.main()
