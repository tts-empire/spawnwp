import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock


path = Path(__file__).parents[1] / "migrations/install-dashboard-update-service.py"
spec = importlib.util.spec_from_file_location("dashboard_update_migration", path)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class DashboardUpdateMigrationTests(unittest.TestCase):
    def test_installs_both_host_units_and_restarts_cockpit(self):
        with tempfile.TemporaryDirectory() as tmp:
            previous = migration.SYSTEMD_ROOT
            migration.SYSTEMD_ROOT = Path(tmp)
            try:
                with mock.patch.object(migration.subprocess, "run") as run:
                    self.assertEqual(0, migration.main())
            finally:
                migration.SYSTEMD_ROOT = previous
            self.assertTrue((Path(tmp) / "wp-cockpit.service").is_file())
            self.assertTrue((Path(tmp) / "spawnwp-update.service").is_file())
            self.assertEqual(
                [
                    mock.call(["systemctl", "daemon-reload"], check=True),
                    mock.call(["systemctl", "restart", "wp-cockpit"], check=True),
                ],
                run.call_args_list,
            )


if __name__ == "__main__":
    unittest.main()
