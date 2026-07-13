import importlib.util
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


path = Path(__file__).parents[1] / "scripts/php-switch-progress.py"
spec = importlib.util.spec_from_file_location("php_switch_progress", path)
progress = importlib.util.module_from_spec(spec)
spec.loader.exec_module(progress)


class FakeProcess:
    def __init__(self, lines, returncode=0):
        self.stdout = iter(lines)
        self.returncode = returncode

    def wait(self):
        return self.returncode


class PhpSwitchProgressTests(unittest.TestCase):
    def project(self, root: Path) -> Path:
        project = root / "site"
        project.mkdir()
        (project / "compose.yaml").write_text("services: {}\n")
        (project / ".env").write_text("PHP_VERSION=8.3\nWORDPRESS_SERIES=7\n")
        return project

    def test_structured_build_progress_is_monotonic(self):
        lines = [
            json.dumps({"id": "pull", "status": "Downloading", "current": 25, "total": 100}) + "\n",
            json.dumps({"id": "pull", "status": "Downloading", "current": 80, "total": 100}) + "\n",
            json.dumps({"id": "export", "status": "Exporting layers"}) + "\n",
        ]
        output = StringIO()
        with mock.patch.object(progress.subprocess, "Popen", return_value=FakeProcess(lines)), redirect_stdout(output):
            self.assertEqual(0, progress.command(["docker"], Path("/tmp"), structured=True))
        events = [json.loads(line.removeprefix(progress.PREFIX)) for line in output.getvalue().splitlines()]
        percentages = [event["percent"] for event in events if event["type"] == "progress" and event["percent"] is not None]
        self.assertEqual(percentages, sorted(percentages))

    def test_failed_first_build_restores_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = self.project(root)
            output = StringIO()
            with mock.patch.object(progress.subprocess, "run",
                                   return_value=SimpleNamespace(returncode=1, stdout="")), \
                 mock.patch.object(progress, "command", return_value=1), redirect_stdout(output):
                result = progress.switch(project, "8.4", root / "locks")
            self.assertEqual(1, result)
            self.assertEqual("PHP_VERSION=8.3\nWORDPRESS_SERIES=7\n", (project / ".env").read_text())

    def test_cached_switch_reaches_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = self.project(root)
            output = StringIO()
            with mock.patch.object(progress.subprocess, "run",
                                   return_value=SimpleNamespace(returncode=0, stdout="")), \
                 mock.patch.object(progress, "run_simple", return_value=0), \
                 mock.patch.object(progress, "healthy", return_value=True), redirect_stdout(output):
                result = progress.switch(project, "8.4", root / "locks")
            self.assertEqual(0, result)
            self.assertIn("PHP_VERSION=8.4", (project / ".env").read_text())
            self.assertIn('"type":"complete"', output.getvalue())

    def test_switch_stamps_the_context_hash_and_the_image_suffix(self):
        """A switch must build with SPAWNWP_CONTEXT_HASH set.

        It used to build without it, so compose stamped the image label "dev" and
        every later deploy on that PHP version saw a cache miss and rebuilt from
        scratch (~5 min). It must also record WP_IMAGE_SUFFIX, which is how compose
        resolves the image tag — sites created before 0.5.20 have no such line.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = self.project(root)
            (project / ".env").write_text("PHP_VERSION=8.3\nWP_VERSION=7.0.1\n")
            answers = {"tag": "wp-dev-php:8.4-wp7.0.1", "suffix": "-wp7.0.1", "hash": "abc123def456"}

            def fake_run(args, **kwargs):
                if args[:2] == ["bash", "scripts/lib-image.sh"]:
                    return SimpleNamespace(returncode=0, stdout=answers[args[2]] + "\n")
                return SimpleNamespace(returncode=0, stdout="")

            output = StringIO()
            with mock.patch.object(progress.subprocess, "run", side_effect=fake_run), \
                 mock.patch.object(progress, "run_simple", return_value=0), \
                 mock.patch.object(progress, "healthy", return_value=True), \
                 mock.patch.dict(progress.os.environ, {}, clear=False), redirect_stdout(output):
                result = progress.switch(project, "8.4", root / "locks")
                self.assertEqual("abc123def456", progress.os.environ["SPAWNWP_CONTEXT_HASH"])
            self.assertEqual(0, result)
            self.assertIn("WP_IMAGE_SUFFIX=-wp7.0.1", (project / ".env").read_text())


if __name__ == "__main__":
    unittest.main()
