import asyncio
import importlib
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from starlette.requests import Request


RUNTIME = Path(__file__).parents[1]
sys.path.insert(0, str(RUNTIME))
IMPORT_TEMP = tempfile.TemporaryDirectory()
STATIC = Path(IMPORT_TEMP.name)
(STATIC / "assets").mkdir()
os.environ["SPAWNWP_STATIC_DIR"] = str(STATIC)

cockpit = importlib.import_module("app")
provision = importlib.import_module("provision")
ingest = importlib.import_module("ingest")


class ProvisionPrimitiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.old_projects = cockpit.PROJECTS_ROOT
        cockpit.PROJECTS_ROOT = root / "srv"
        cockpit.PROJECTS_ROOT.mkdir()
        os.environ["SPAWNWP_INGEST_DB"] = str(root / "ingest.sqlite3")

    def tearDown(self):
        cockpit.PROJECTS_ROOT = self.old_projects
        self.temp.cleanup()

    def test_schema_migrates_managed_access_fields(self):
        db = ingest._connect()
        try:
            db.execute(
                "CREATE TABLE provision_sites(project TEXT PRIMARY KEY,connection_id TEXT NOT NULL,"
                "status TEXT NOT NULL,expires_at INTEGER NOT NULL,created_at INTEGER NOT NULL)",
            )
            provision._initialize(db)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(provision_sites)")}
        finally:
            db.close()
        self.assertIn("username", columns)
        self.assertIn("credentials_mode", columns)

    def test_managed_execution_omits_password_and_resets_lifetime(self):
        payload = provision.ProvisionRequest(
            blueprint="development", access_profile="restricted-admin",
            credentials_mode="managed", expires_seconds=1800,
        )

        def create(site, timeout):
            del timeout
            project = cockpit.PROJECTS_ROOT / site.name
            project.mkdir()
            (project / "compose.yaml").write_text("services: {}\n")
            (project / "Makefile").write_text(":\n")
            (project / ".env").write_text(
                f"WP_HOME=https://example.test/{site.name}\nSPAWNWP_EXPIRES=1\n",
            )

        with mock.patch.object(cockpit, "run_project_creation", side_effect=create), \
                mock.patch.object(cockpit, "create_wp_user", return_value={
                    "username": "demo-user", "password": "never-return-this",
                }), mock.patch.object(cockpit, "_autologin_installed", return_value=False), \
                mock.patch.object(cockpit, "install_autologin") as autologin, \
                mock.patch.object(cockpit, "install_restricted_admin") as guard, \
                mock.patch.object(provision, "_finish") as finish:
            result = provision._execute_reserved("connection", "request-key", "demo-site", payload)
        self.assertNotIn("password", result)
        self.assertNotIn("magic_link", result)
        self.assertGreaterEqual(result["expires_at"], int(time.time()) + 1795)
        guard.assert_called_once()
        autologin.assert_called_once()
        self.assertEqual(finish.call_args.args[3], "demo-user")

    def test_magic_link_requires_owned_managed_site(self):
        project = cockpit.PROJECTS_ROOT / "demo-site"
        project.mkdir()
        (project / "compose.yaml").write_text("services: {}\n")
        (project / "Makefile").write_text(":\n")
        now = int(time.time())
        db = ingest._connect()
        try:
            provision._initialize(db)
            db.execute(
                "INSERT INTO provision_sites(project,connection_id,status,expires_at,created_at,"
                "username,credentials_mode) VALUES (?,?,?,?,?,?,?)",
                ("demo-site", "owner", "active", now + 1800, now, "demo-user", "managed"),
            )
            db.commit()
        finally:
            db.close()
        request = Request({
            "type": "http", "method": "POST", "scheme": "https",
            "path": "/api/provision/sites/demo-site/magic-link", "raw_path": b"",
            "query_string": b"", "headers": [], "server": ("example.test", 443),
            "client": ("127.0.0.1", 1234),
        })
        authorization = mock.AsyncMock(return_value=({"id": "owner"}, b""))
        with mock.patch.object(provision.machine_auth, "authorize", authorization), \
                mock.patch.object(cockpit, "mint_magic_login", return_value={
                    "project": "demo-site", "url": "https://example.test/magic", "expires_in": 120,
                }) as mint:
            result = asyncio.run(provision.provision_magic_link("demo-site", request))
        self.assertEqual(result["expires_in"], 120)
        mint.assert_called_once_with(project, "demo-user")


if __name__ == "__main__":
    unittest.main()
