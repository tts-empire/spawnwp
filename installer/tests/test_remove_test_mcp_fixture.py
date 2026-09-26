import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


path = Path(__file__).parents[1] / "migrations/remove-test-mcp-fixture.py"
spec = importlib.util.spec_from_file_location("remove_test_mcp_fixture", path)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class RemoveTestMcpFixtureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        migration.MODULES_ROOT = root / "modules"
        migration.STATE_ROOT = root / "state"
        migration.SPAWNWP_CLI = root / "spawnwp"
        migration.SPAWNWP_CLI.write_text("")

    def tearDown(self):
        self.temporary.cleanup()

    def write_manifest(self, **updates):
        current = migration.MODULES_ROOT / migration.MODULE_ID / "current"
        current.mkdir(parents=True)
        manifest = {
            "id": migration.MODULE_ID,
            "name": migration.FIXTURE_NAME,
            "description": migration.FIXTURE_DESCRIPTION,
        }
        manifest.update(updates)
        (current / "module.json").write_text(json.dumps(manifest))
        (migration.STATE_ROOT / migration.MODULE_ID).mkdir(parents=True)

    def test_removes_exact_fixture(self):
        self.write_manifest()
        with mock.patch.object(migration.subprocess, "run", return_value=mock.Mock(returncode=0)) as run:
            self.assertEqual(0, migration.main())
        run.assert_called_once_with(
            [str(migration.SPAWNWP_CLI), "module", "remove", migration.MODULE_ID, "--force", "--purge"],
            capture_output=True, text=True,
        )
        self.assertFalse((migration.MODULES_ROOT / migration.MODULE_ID).exists())
        self.assertFalse((migration.STATE_ROOT / migration.MODULE_ID).exists())

    def test_leaves_non_fixture_module_alone(self):
        self.write_manifest(name="A real MCP module")
        with mock.patch.object(migration.subprocess, "run") as run:
            self.assertEqual(0, migration.main())
        run.assert_not_called()
        self.assertTrue((migration.MODULES_ROOT / migration.MODULE_ID / "current" / "module.json").is_file())


if __name__ == "__main__":
    unittest.main()
