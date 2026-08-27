import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock


path = Path(__file__).parents[1] / "migrations/add-module-include.py"
spec = importlib.util.spec_from_file_location("add_module_include", path)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class AddModuleIncludeTests(unittest.TestCase):
    def test_discovers_enabled_cockpit_vhost_by_domain(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            available = root / "available"
            enabled = root / "enabled"
            available.mkdir()
            enabled.mkdir()
            cockpit = available / "custom-name"
            cockpit.write_text(
                "server {\n    server_name cockpit.example.test;\n"
                "    location /assets/ {\n        proxy_pass http://127.0.0.1:9393;\n    }\n}\n"
            )
            (enabled / "spawnwp.com").symlink_to(cockpit)
            config = root / "config.env"
            config.write_text("COCKPIT_DOMAIN=cockpit.example.test\n")
            previous = (migration.NGINX_CONF, migration.SITES_ENABLED,
                        migration.SPAWNWP_CONFIG, migration.MODULE_DIR)
            migration.NGINX_CONF = None
            migration.SITES_ENABLED = enabled
            migration.SPAWNWP_CONFIG = config
            migration.MODULE_DIR = root / "modules"
            try:
                success = mock.Mock(returncode=0, stderr="")
                with mock.patch.object(migration.subprocess, "run", return_value=success):
                    migration.main()
            finally:
                (migration.NGINX_CONF, migration.SITES_ENABLED,
                 migration.SPAWNWP_CONFIG, migration.MODULE_DIR) = previous
            self.assertIn("spawnwp-modules/*.conf", cockpit.read_text())

    def test_adds_include_idempotently_and_reloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nginx = root / "spawnwp"
            modules = root / "modules"
            nginx.write_text(
                "server {\n"
                "    server_name cockpit.example.test;\n"
                "    location /assets/ {\n"
                "        proxy_pass http://127.0.0.1:9393/assets/;\n"
                "    }\n"
                "}\n"
            )
            previous = migration.NGINX_CONF, migration.MODULE_DIR
            migration.NGINX_CONF, migration.MODULE_DIR = nginx, modules
            try:
                success = mock.Mock(returncode=0, stderr="")
                with mock.patch.object(migration.subprocess, "run", return_value=success) as run:
                    migration.main()
                    first = nginx.read_text()
                    migration.main()
            finally:
                migration.NGINX_CONF, migration.MODULE_DIR = previous
            self.assertTrue(modules.is_dir())
            self.assertEqual(first.count("spawnwp-modules/*.conf"), 1)
            self.assertEqual(first, nginx.read_text())
            self.assertEqual(run.call_count, 2)

    def test_restores_configuration_when_nginx_rejects_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nginx = root / "spawnwp"
            original = "server {\n    location /assets/ { }\n}\n"
            nginx.write_text(original)
            previous = migration.NGINX_CONF, migration.MODULE_DIR
            migration.NGINX_CONF, migration.MODULE_DIR = nginx, root / "modules"
            try:
                failed = mock.Mock(returncode=1, stderr="bad nginx")
                with mock.patch.object(migration.subprocess, "run", return_value=failed), \
                        self.assertRaises(SystemExit):
                    migration.main()
            finally:
                migration.NGINX_CONF, migration.MODULE_DIR = previous
            self.assertEqual(nginx.read_text(), original)


if __name__ == "__main__":
    unittest.main()
