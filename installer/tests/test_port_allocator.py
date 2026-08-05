import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


path = Path(__file__).parents[1] / "port_allocator.py"
spec = importlib.util.spec_from_file_location("port_allocator", path)
allocator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(allocator)


def project(root: Path, name: str, **ports: int) -> Path:
    target = root / name
    target.mkdir()
    (target / ".env").write_text(
        "\n".join(f"{key}={value}" for key, value in ports.items()) + "\n",
        encoding="utf-8",
    )
    return target


class PortAllocatorTests(unittest.TestCase):
    def test_stopped_projects_reserve_all_declared_ports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project(root, "alpha", WEB_PORT=8081, MAILPIT_PORT=8026, ADMINER_PORT=9002)
            self.assertEqual(
                {"WEB_PORT": 8082, "MAILPIT_PORT": 8027, "ADMINER_PORT": 9003},
                allocator.allocate_ports(root, set()),
            )

    def test_listening_ports_and_cross_kind_claims_are_global(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project(root, "custom", WEB_PORT=8026, MAILPIT_PORT=9002, ADMINER_PORT=8081)
            self.assertEqual(
                {"WEB_PORT": 8083, "MAILPIT_PORT": 8028, "ADMINER_PORT": 9004},
                allocator.allocate_ports(root, {8082, 8027, 9003}),
            )

    def test_last_env_value_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text("WEB_PORT=8081\nWEB_PORT=8087\n", encoding="utf-8")
            self.assertEqual({"WEB_PORT": 8087}, allocator.read_env_ports(env_file))

    def test_invalid_existing_port_stops_allocation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "broken"
            target.mkdir()
            (target / ".env").write_text("WEB_PORT=not-a-port\n", encoding="utf-8")
            with self.assertRaisesRegex(allocator.PortAllocationError, "WEB_PORT must be a numeric"):
                allocator.allocate_ports(root, set())

    def test_ss_output_parser_handles_ipv4_ipv6_and_wildcards(self):
        result = subprocess.CompletedProcess(
            ["ss"], 0,
            "LISTEN 0 4096 127.0.0.1:8081 0.0.0.0:*\n"
            "LISTEN 0 4096 [::]:8026 [::]:*\n",
            "",
        )
        with mock.patch.object(allocator.subprocess, "run", return_value=result):
            self.assertEqual({8081, 8026}, allocator.listening_ports())

    def test_cli_shell_output_is_safe_assignments(self):
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(allocator, "listening_ports", return_value=set()):
            output = []
            with mock.patch("builtins.print", side_effect=lambda value, **_: output.append(value)):
                self.assertEqual(0, allocator.main(["allocate", "--projects-root", tmp, "--shell"]))
            self.assertEqual(
                ["WEB_PORT=8081", "MAILPIT_PORT=8026", "ADMINER_PORT=9002"],
                output,
            )


if __name__ == "__main__":
    unittest.main()
