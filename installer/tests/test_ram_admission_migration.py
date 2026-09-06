import importlib.util
import os
import tempfile
import unittest
from pathlib import Path


path = Path(__file__).parents[1] / "migrations/add-ram-admission-limits.py"
spec = importlib.util.spec_from_file_location("ram_admission_migration", path)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


COMPOSE = """name: test
services:
  nginx:
    deploy:
      resources:
        limits:
          memory: 128M
  php:
    deploy:
      resources:
        limits:
          memory: 512M
  db:
    deploy:
      resources:
        limits:
          memory: 384M
"""


class RamAdmissionMigrationTests(unittest.TestCase):
    def site(self, memory_limit="256M"):
        temporary = tempfile.TemporaryDirectory()
        site = Path(temporary.name) / "site"
        (site / "docker/php").mkdir(parents=True)
        (site / "compose.yaml").write_text(COMPOSE)
        (site / ".env").write_text("COMPOSE_PROJECT_NAME=site\n")
        os.chmod(site / ".env", 0o600)
        (site / "docker/php/zz-site.ini").write_text(f"memory_limit = {memory_limit}\n")
        return temporary, site

    def test_default_limit_keeps_512m_container_floor(self):
        temporary, site = self.site()
        self.addCleanup(temporary.cleanup)
        self.assertTrue(migration.upgrade_site(site))
        self.assertIn("memory: ${SPAWNWP_PHP_CONTAINER_MEMORY:-512M}",
                      (site / "compose.yaml").read_text())
        self.assertIn("SPAWNWP_PHP_CONTAINER_MEMORY=512M", (site / ".env").read_text())
        self.assertEqual(0o600, (site / ".env").stat().st_mode & 0o777)

    def test_one_gig_php_limit_gets_1280m_container_cap(self):
        temporary, site = self.site("1G")
        self.addCleanup(temporary.cleanup)
        migration.upgrade_site(site)
        self.assertIn("SPAWNWP_PHP_CONTAINER_MEMORY=1280M", (site / ".env").read_text())

    def test_second_run_is_noop(self):
        temporary, site = self.site("512M")
        self.addCleanup(temporary.cleanup)
        self.assertTrue(migration.upgrade_site(site))
        compose = (site / "compose.yaml").read_text()
        env = (site / ".env").read_text()
        self.assertFalse(migration.upgrade_site(site))
        self.assertEqual(compose, (site / "compose.yaml").read_text())
        self.assertEqual(env, (site / ".env").read_text())


if __name__ == "__main__":
    unittest.main()
