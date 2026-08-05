import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


path = Path(__file__).parents[1] / "migrations/repair-duplicate-project-ports.py"
spec = importlib.util.spec_from_file_location("duplicate_port_migration", path)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def add_project(root: Path, name: str, web: int, mail: int, admin: int) -> Path:
    project = root / name
    project.mkdir()
    (project / ".env").write_text(
        f"COMPOSE_PROJECT_NAME={name}\n"
        f"WEB_PORT={web}\nMAILPIT_PORT={mail}\nADMINER_PORT={admin}\n",
        encoding="utf-8",
    )
    os.chmod(project / ".env", 0o600)
    return project


def nginx_block(name: str, web: int, mail: int, admin: int) -> str:
    return f"""
    # >>> SPAWNWP SITE {name}
    # ── {name} (port {web})
    location /{name}/wp-json/spawnwp-deploy/v1/ {{
        proxy_pass http://127.0.0.1:{web}/wp-json/spawnwp-deploy/v1/;
    }}
    location /{name}/ {{
        proxy_pass http://127.0.0.1:{web}/;
    }}
    # <<< SPAWNWP SITE {name}
    # >>> SPAWNWP ADMIN {name}
    location /{name}-db/ {{
        proxy_pass http://127.0.0.1:{admin}/;
    }}
    location /{name}-mail/ {{
        proxy_pass http://127.0.0.1:{mail};
    }}
    # <<< SPAWNWP ADMIN {name}
"""


class CommandRunner:
    def __init__(self, fail_test: bool = False):
        self.calls = []
        self.fail_test = fail_test

    def __call__(self, command):
        self.calls.append(command)
        if command == ["nginx", "-t"] and self.fail_test:
            self.fail_test = False
            return subprocess.CompletedProcess(command, 1, "", "invalid nginx")
        return subprocess.CompletedProcess(command, 0, "", "")


class DuplicatePortMigrationTests(unittest.TestCase):
    def fixture(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name) / "srv"
        root.mkdir()
        alpha = add_project(root, "alpha", 8081, 8026, 9002)
        beta = add_project(root, "beta", 8081, 8026, 9002)
        nginx = Path(temporary.name) / "spawnwp.conf"
        nginx.write_text(
            nginx_block("alpha", 8081, 8026, 9002)
            + nginx_block("beta", 8081, 8026, 9002),
            encoding="utf-8",
        )
        return temporary, root, alpha, beta, nginx

    def test_running_claimant_keeps_ports_and_stopped_site_is_repaired(self):
        temporary, root, alpha, beta, nginx = self.fixture()
        self.addCleanup(temporary.cleanup)
        runner = CommandRunner()

        plan = migration.repair_duplicate_ports(
            root,
            nginx,
            active_ports={8081, 8026, 9002},
            service_reader=lambda project: {"web", "mailpit", "adminer"} if project == beta else set(),
            command_runner=runner,
        )

        self.assertEqual(
            {
                "WEB_PORT": (8081, 8082),
                "MAILPIT_PORT": (8026, 8027),
                "ADMINER_PORT": (9002, 9003),
            },
            plan[alpha],
        )
        self.assertIn("WEB_PORT=8082", (alpha / ".env").read_text())
        self.assertIn("MAILPIT_PORT=8027", (alpha / ".env").read_text())
        self.assertIn("ADMINER_PORT=9003", (alpha / ".env").read_text())
        self.assertIn("WEB_PORT=8081", (beta / ".env").read_text())
        conf = nginx.read_text()
        self.assertIn("alpha (port 8082)", conf)
        self.assertEqual(2, conf.count("127.0.0.1:8082"))
        self.assertIn("127.0.0.1:8027", conf)
        self.assertIn("127.0.0.1:9003", conf)
        self.assertEqual(0o600, (alpha / ".env").stat().st_mode & 0o777)
        self.assertEqual([["nginx", "-t"], ["systemctl", "reload", "nginx"]], runner.calls)

    def test_no_running_claimant_keeps_lexicographically_first_project(self):
        temporary, root, alpha, beta, nginx = self.fixture()
        self.addCleanup(temporary.cleanup)
        runner = CommandRunner()
        plan = migration.repair_duplicate_ports(
            root, nginx, active_ports=set(), service_reader=lambda _: set(), command_runner=runner
        )
        self.assertNotIn(alpha, plan)
        self.assertIn(beta, plan)
        self.assertIn("WEB_PORT=8081", (alpha / ".env").read_text())
        self.assertIn("WEB_PORT=8082", (beta / ".env").read_text())

    def test_second_run_is_a_noop(self):
        temporary, root, _, _, nginx = self.fixture()
        self.addCleanup(temporary.cleanup)
        migration.repair_duplicate_ports(
            root, nginx, active_ports=set(), service_reader=lambda _: set(), command_runner=CommandRunner()
        )
        second = CommandRunner()
        self.assertEqual(
            {},
            migration.repair_duplicate_ports(
                root, nginx, active_ports=set(), service_reader=lambda _: set(), command_runner=second
            ),
        )
        self.assertEqual([], second.calls)

    def test_nginx_validation_failure_restores_every_file(self):
        temporary, root, alpha, beta, nginx = self.fixture()
        self.addCleanup(temporary.cleanup)
        originals = {
            alpha: (alpha / ".env").read_text(),
            beta: (beta / ".env").read_text(),
            nginx: nginx.read_text(),
        }
        runner = CommandRunner(fail_test=True)
        with self.assertRaisesRegex(migration.PortRepairError, "invalid nginx"):
            migration.repair_duplicate_ports(
                root, nginx, active_ports=set(), service_reader=lambda _: set(), command_runner=runner
            )
        self.assertEqual(originals[alpha], (alpha / ".env").read_text())
        self.assertEqual(originals[beta], (beta / ".env").read_text())
        self.assertEqual(originals[nginx], nginx.read_text())
        self.assertEqual(
            [
                ["nginx", "-t"],
                ["nginx", "-t"],
                ["systemctl", "reload", "nginx"],
            ],
            runner.calls,
        )

    def test_missing_markers_fail_before_writing(self):
        temporary, root, alpha, beta, nginx = self.fixture()
        self.addCleanup(temporary.cleanup)
        nginx.write_text(nginx.read_text().replace("# >>> SPAWNWP SITE beta", "# missing"))
        alpha_before = (alpha / ".env").read_text()
        beta_before = (beta / ".env").read_text()
        nginx_before = nginx.read_text()
        runner = CommandRunner()
        with self.assertRaisesRegex(migration.PortRepairError, "nginx site markers not found"):
            migration.repair_duplicate_ports(
                root, nginx, active_ports=set(), service_reader=lambda _: set(), command_runner=runner
            )
        self.assertEqual(alpha_before, (alpha / ".env").read_text())
        self.assertEqual(beta_before, (beta / ".env").read_text())
        self.assertEqual(nginx_before, nginx.read_text())
        self.assertEqual([], runner.calls)

    def test_multiple_running_claimants_are_rejected_before_writing(self):
        temporary, root, alpha, beta, nginx = self.fixture()
        self.addCleanup(temporary.cleanup)
        with self.assertRaisesRegex(migration.PortRepairError, "multiple running web services"):
            migration.repair_duplicate_ports(
                root,
                nginx,
                active_ports={8081},
                service_reader=lambda _: {"web"},
                command_runner=CommandRunner(),
            )
        self.assertIn("WEB_PORT=8081", (alpha / ".env").read_text())
        self.assertIn("WEB_PORT=8081", (beta / ".env").read_text())


if __name__ == "__main__":
    unittest.main()
