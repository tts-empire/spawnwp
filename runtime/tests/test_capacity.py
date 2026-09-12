import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

RUNTIME = Path(__file__).parents[1]
sys.path.insert(0, str(RUNTIME))
import capacity


class CapacityPolicyTests(unittest.TestCase):
    def setUp(self):
        capacity._reservations.clear()

    def tearDown(self):
        capacity._reservations.clear()

    def test_missing_admission_policy_defaults_to_enforce(self):
        with mock.patch.object(capacity, "CONFIG_ENV", Path("/does/not/exist")):
            self.assertEqual(capacity.ADMISSION_ENFORCE, capacity.admission_policy())

    def test_invalid_admission_policy_fails_closed(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as config:
            config.write("SPAWNWP_RAM_ADMISSION=maybe\n")
            config_path = Path(config.name)
        try:
            with mock.patch.object(capacity, "CONFIG_ENV", config_path):
                with self.assertRaises(capacity.CapacityError):
                    capacity.admission_policy()
        finally:
            config_path.unlink(missing_ok=True)

    def test_system_reserve_is_bounded(self):
        self.assertEqual(512 * capacity.MIB, capacity.system_reserve_bytes(2 * capacity.GIB))
        self.assertEqual(1 * capacity.GIB, capacity.system_reserve_bytes(8 * capacity.GIB))

    def test_php_cap_has_floor_and_headroom(self):
        self.assertEqual(512 * capacity.MIB, capacity.php_container_memory_bytes("16M"))
        self.assertEqual(512 * capacity.MIB, capacity.php_container_memory_bytes("256M"))
        self.assertEqual(768 * capacity.MIB, capacity.php_container_memory_bytes("512M"))
        self.assertEqual(1280 * capacity.MIB, capacity.php_container_memory_bytes("1G"))

    def test_stopped_sites_consume_zero_and_pending_reservations_close_race(self):
        root = Path("/srv")
        limits = {"nginx": 128 * capacity.MIB, "php": 512 * capacity.MIB,
                  "db": 384 * capacity.MIB, "mailpit": 64 * capacity.MIB,
                  "adminer": 48 * capacity.MIB}
        with mock.patch.object(capacity, "host_total_bytes", return_value=2 * capacity.GIB), \
             mock.patch.object(capacity, "running_container_limits", return_value=[]), \
             mock.patch.object(capacity, "configured_service_limits", return_value=limits):
            token, requested = capacity.reserve_new_project(root, root / "wp-dev")
            self.assertEqual(1136 * capacity.MIB, requested)
            with self.assertRaises(capacity.CapacityError) as ctx:
                capacity.reserve_new_project(root, root / "wp-dev")
            self.assertIn("committed 1.1 GiB", str(ctx.exception))
            self.assertIn("available 400 MiB", str(ctx.exception))
            capacity.release_reservation(token)
            second, _ = capacity.reserve_new_project(root, root / "wp-dev")
            self.assertIsNotNone(second)
            capacity.release_reservation(second)

    def test_advisory_allows_overcommit_and_reports_policy(self):
        root = Path("/srv")
        limits = {"nginx": 128 * capacity.MIB, "php": 512 * capacity.MIB,
                  "db": 384 * capacity.MIB, "mailpit": 64 * capacity.MIB,
                  "adminer": 48 * capacity.MIB}
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as config:
            config.write("SPAWNWP_RAM_ADMISSION=advisory\n")
            config_path = Path(config.name)
        try:
            with mock.patch.object(capacity, "CONFIG_ENV", config_path), \
                 mock.patch.object(capacity, "host_total_bytes", return_value=2 * capacity.GIB), \
                 mock.patch.object(capacity, "running_container_limits", return_value=[]), \
                 mock.patch.object(capacity, "configured_service_limits", return_value=limits):
                token, requested = capacity.reserve_new_project(root, root / "wp-dev")
                self.assertEqual(1136 * capacity.MIB, requested)
                self.assertIsNotNone(token)
                second, _ = capacity.reserve_new_project(root, root / "wp-dev-2")
                self.assertIsNotNone(second)
                snapshot = capacity.capacity_snapshot(root)
                self.assertEqual(capacity.ADMISSION_ADVISORY, snapshot["admission_policy"])
                self.assertTrue(snapshot["overcommitted"])
                self.assertEqual(
                    capacity.ADMISSION_ADVISORY,
                    capacity.public_snapshot(root)["admission_policy"],
                )
                capacity.release_reservation(token)
                capacity.release_reservation(second)
        finally:
            config_path.unlink(missing_ok=True)

    def test_up_reserves_only_missing_container_limits(self):
        root = Path("/srv")
        project = root / "one"
        records = [
            {"project": str(project), "service": "php", "bytes": 512 * capacity.MIB},
            {"project": str(project), "service": "db", "bytes": 384 * capacity.MIB},
        ]
        limits = {"nginx": 128 * capacity.MIB, "php": 512 * capacity.MIB,
                  "db": 384 * capacity.MIB, "mailpit": 64 * capacity.MIB,
                  "adminer": 48 * capacity.MIB}
        with mock.patch.object(capacity, "host_total_bytes", return_value=4 * capacity.GIB), \
             mock.patch.object(capacity, "running_container_limits", return_value=records), \
             mock.patch.object(capacity, "configured_service_limits", return_value=limits):
            token, requested = capacity.reserve_project_start(root, project)
        self.assertEqual(240 * capacity.MIB, requested)
        capacity.release_reservation(token)

    def test_php_resize_only_reserves_positive_delta_while_running(self):
        root = Path("/srv")
        project = root / "one"
        with mock.patch.object(capacity, "host_total_bytes", return_value=4 * capacity.GIB), \
             mock.patch.object(capacity, "running_container_limits", return_value=[]):
            token, requested = capacity.reserve_php_resize(root, project, 1280 * capacity.MIB)
        self.assertIsNone(token)
        self.assertEqual(0, requested)

        records = [{"project": str(project), "service": "php", "bytes": 512 * capacity.MIB}]
        with mock.patch.object(capacity, "host_total_bytes", return_value=4 * capacity.GIB), \
             mock.patch.object(capacity, "running_container_limits", return_value=records):
            token, requested = capacity.reserve_php_resize(root, project, 768 * capacity.MIB)
        self.assertEqual(256 * capacity.MIB, requested)
        capacity.release_reservation(token)

    def test_docker_records_include_only_direct_spawnwp_projects(self):
        inspect = [{
            "Id": "a" * 64, "State": {"Running": True},
            "Config": {"Labels": {
                "com.docker.compose.project.working_dir": "/srv/site-one",
                "com.docker.compose.service": "php",
            }},
            "HostConfig": {"Memory": 536870912},
        }, {
            "Id": "b" * 64, "State": {"Running": True},
            "Config": {"Labels": {
                "com.docker.compose.project.working_dir": "/opt/other",
                "com.docker.compose.service": "db",
            }},
            "HostConfig": {"Memory": 999},
        }]
        calls = [
            mock.Mock(returncode=0, stdout="a\nb\n"),
            mock.Mock(returncode=0, stdout=json.dumps(inspect)),
        ]
        with mock.patch.object(capacity.subprocess, "run", side_effect=calls):
            records = capacity.running_container_limits(Path("/srv"))
        self.assertEqual(1, len(records))
        self.assertEqual("php", records[0]["service"])
        self.assertEqual(512 * capacity.MIB, records[0]["bytes"])


if __name__ == "__main__":
    unittest.main()
