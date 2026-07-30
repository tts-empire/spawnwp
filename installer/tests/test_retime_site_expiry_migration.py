import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock


path = Path(__file__).parents[1] / "migrations/retime-site-expiry.py"
spec = importlib.util.spec_from_file_location("retime_site_expiry_migration", path)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class RetimeSiteExpiryMigrationTests(unittest.TestCase):
    def test_installs_units_and_restarts_timer(self):
        with tempfile.TemporaryDirectory() as tmp:
            previous = migration.SYSTEMD_ROOT
            migration.SYSTEMD_ROOT = Path(tmp)
            try:
                with mock.patch.object(migration.subprocess, "run") as run:
                    self.assertEqual(0, migration.main())
            finally:
                migration.SYSTEMD_ROOT = previous
            timer = (Path(tmp) / "spawnwp-site-expiry.timer").read_text()
            self.assertIn("OnCalendar=*:0/5", timer)
            self.assertEqual(
                [
                    mock.call(["systemctl", "daemon-reload"], check=True),
                    mock.call(["systemctl", "enable", "spawnwp-site-expiry.timer"], check=True),
                    mock.call(["systemctl", "restart", "spawnwp-site-expiry.timer"], check=True),
                ],
                run.call_args_list,
            )


if __name__ == "__main__":
    unittest.main()
