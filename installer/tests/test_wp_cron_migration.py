import importlib.util
import os
import unittest
from pathlib import Path


path = Path(__file__).parents[1] / "migrations/enable-site-cron-and-mail-capture.py"
os.environ["SPAWNWP_MAIL_CAPTURE_SOURCE"] = str(
    Path(__file__).parents[2] / "runtime/scripts/mail-capture.php"
)
spec = importlib.util.spec_from_file_location("wp_cron_migration", path)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class WpCronMigrationTests(unittest.TestCase):
    def test_detects_legacy_disabled_cron_in_live_php_environment(self):
        env = ["WORDPRESS_CONFIG_EXTRA=define('DISABLE_WP_CRON', true);\ndefine('WP_DEBUG', true);"]
        self.assertTrue(migration.has_disabled_wp_cron(env))

    def test_does_not_recreate_php_after_live_override_is_gone(self):
        env = ["WORDPRESS_CONFIG_EXTRA=define('WP_DEBUG', true);\ndefine('WP_HOME', 'https://example.test');"]
        self.assertFalse(migration.has_disabled_wp_cron(env))

    def test_does_not_treat_false_as_disabled(self):
        env = ["WORDPRESS_CONFIG_EXTRA=define('DISABLE_WP_CRON', false);"]
        self.assertFalse(migration.has_disabled_wp_cron(env))


if __name__ == "__main__":
    unittest.main()
