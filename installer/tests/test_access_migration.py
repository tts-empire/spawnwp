import importlib.util
import unittest
from pathlib import Path


path = Path(__file__).parents[1] / "migrations/remove-legacy-access.py"
spec = importlib.util.spec_from_file_location("access_migration", path)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class AccessMigrationTests(unittest.TestCase):
    def test_rewrite_removes_legacy_access_and_adds_rate_limit(self):
        original = """map $http_upgrade $connection_upgrade { default upgrade; '' close; }
server {
    auth_basic "SpawnWP cockpit";
    auth_basic_user_file /etc/nginx/.spawnwp-htpasswd;
    # __COCKPIT_PER_SITE__
    location / {
        include /etc/nginx/cockpit-allowed.conf;
        error_page 401 =303 /login;
    }
}
"""
        updated = migration.rewrite_nginx(original)
        self.assertNotIn("auth_basic", updated)
        self.assertNotIn("cockpit-allowed", updated)
        self.assertIn("limit_req_zone", updated)
        self.assertIn("limit_req zone=spawnwp_auth", updated)
        self.assertIn("location = /_spawnwp_auth", updated)
        self.assertIn("location @spawnwp_login", updated)
        self.assertIn("error_page 401 = @spawnwp_login", updated)
        self.assertEqual(updated, migration.rewrite_nginx(updated))

    def test_discovers_enabled_spawnwp_vhost(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            available = root / "available"
            available.write_text("# __COCKPIT_PER_SITE__\n")
            enabled = root / "enabled"
            enabled.mkdir()
            (enabled / "default").symlink_to(available)
            old_enabled = migration.NGINX_ENABLED
            try:
                migration.NGINX_ENABLED = enabled
                self.assertEqual(migration.nginx_targets(), [available])
            finally:
                migration.NGINX_ENABLED = old_enabled


if __name__ == "__main__":
    unittest.main()
