import os
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "site-expiry.sh"


class SiteExpiryTests(unittest.TestCase):
    def test_permanent_site_does_not_stop_later_expiry(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            projects = root / "srv"
            primary = projects / "wp-dev"
            scripts = primary / "scripts"
            bin_dir = root / "bin"
            scripts.mkdir(parents=True)
            bin_dir.mkdir()

            permanent = projects / "a-permanent"
            expired = projects / "b-expired"
            for project in (permanent, expired):
                project.mkdir()
                (project / "compose.yaml").write_text("services: {}\n")
            (permanent / ".env").write_text("WP_HOME=https://example.test/a-permanent\n")
            # A hand-edited .env may legitimately omit the final newline.
            (expired / ".env").write_text("SPAWNWP_EXPIRES=100")

            destroy_log = root / "destroy.log"
            (scripts / "destroy-project.sh").write_text(
                "#!/usr/bin/env bash\n"
                "printf '%s\\n' \"$1\" >> \"$SPAWNWP_TEST_DESTROY_LOG\"\n"
            )
            (scripts / "lib-metrics.sh").write_text("metric_incr() { :; }\n")
            (bin_dir / "docker").write_text("#!/usr/bin/env bash\nexit 0\n")
            os.chmod(bin_dir / "docker", 0o755)

            env = {
                **os.environ,
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "SPAWNWP_PROJECTS_ROOT": str(projects),
                "SPAWNWP_PRIMARY_PROJECT": str(primary),
                "SPAWNWP_PROJECT_LOCK": str(root / "projects.lock"),
                "SPAWNWP_NOW": "200",
                "SPAWNWP_TEST_DESTROY_LOG": str(destroy_log),
            }
            result = subprocess.run(
                ["bash", str(SCRIPT)], capture_output=True, text=True, env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(destroy_log.read_text(), "b-expired\n")
            self.assertIn("check complete", result.stdout)


if __name__ == "__main__":
    unittest.main()
