import base64
import importlib.machinery
import importlib.util
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.parse import urlparse

from fastapi import FastAPI
from fastapi.testclient import TestClient

# The production cockpit mounts static files at import time. CI has no installed
# /srv/wp-cockpit tree, so give this isolated integration process a real mount.
IMPORT_TEMP = tempfile.TemporaryDirectory()
STATIC_ROOT = Path(IMPORT_TEMP.name)
(STATIC_ROOT / "assets").mkdir()
os.environ["SPAWNWP_STATIC_DIR"] = str(STATIC_ROOT)

import app as cockpit
import ingest
import provision

CLIENT_PATH = Path(__file__).parents[1] / "spawnwp-api"
LOADER = importlib.machinery.SourceFileLoader("spawnwp_api_integration", str(CLIENT_PATH))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
api_client = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(api_client)


class ClientApiIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.projects = root / "srv"
        self.projects.mkdir()
        os.environ.update(
            SPAWNWP_INGEST_DB=str(root / "ingest.db"),
            SPAWNWP_CONFIG=str(root / "config.env"),
            SPAWNWP_VERSION_FILE=str(root / "VERSION"),
            SPAWNWP_PROVISION_MAX_SITES_PER_CONNECTION="3",
        )
        (root / "VERSION").write_text("9.8.7\n")
        self.old_projects_root = cockpit.PROJECTS_ROOT
        cockpit.PROJECTS_ROOT = self.projects
        api = FastAPI()
        api.include_router(ingest.router)
        api.include_router(provision.router)
        self.server = TestClient(api, base_url="https://testserver")
        self.config = root / "client" / "api.json"

    def tearDown(self):
        cockpit.PROJECTS_ROOT = self.old_projects_root
        self.temp.cleanup()

    def bridge(self, url, *, method="GET", body=b"", headers=None, timeout=30):
        del timeout
        response = self.server.request(
            method,
            urlparse(url).path,
            content=body,
            headers=headers or {},
        )
        if response.status_code >= 400:
            raise api_client.ClientError(
                f"API request failed ({response.status_code}): {response.text}"
            )
        return response.json()

    def fake_creation(self, site, timeout=300):
        del timeout
        project = self.projects / site.name
        project.mkdir()
        (project / "compose.yaml").write_text("services: {}\n")
        (project / "Makefile").write_text(":\n")
        (project / ".env").write_text(
            f"WP_HOME=https://testserver/{site.name}\n"
            f"SPAWNWP_EXPIRES={int(time.time()) + site.lifetime_seconds}\n"
        )

    def test_pair_status_provision_and_revoke(self):
        pairing = self.server.post(
            "/api/blueprint-pairings",
            json={"scope": "provision"},
        ).json()["bundle"]
        pair_args = SimpleNamespace(
            pairing_code=pairing,
            config=self.config,
            force=False,
            source_host="ci.example",
            label="Integration test",
        )
        with mock.patch.object(api_client, "http_json", side_effect=self.bridge):
            paired = api_client.command_pair(pair_args)
        self.assertEqual(paired["status"], "paired")

        status_args = SimpleNamespace(config=self.config)
        with mock.patch.object(api_client, "http_json", side_effect=self.bridge):
            status = api_client.command_status(status_args)
        self.assertEqual(status["spawnwp_version"], "9.8.7")
        self.assertEqual(status["defaults"]["role"], "administrator")
        self.assertEqual(status["active_sites"], 0)

        provision_args = SimpleNamespace(
            config=self.config,
            blueprint="development",
            expires=3600,
            role="administrator",
            group="API",
            name="integration-site",
            access_profile="standard",
            credentials_mode="return",
            idempotency_key="integration-request-0001",
            timeout=610,
        )
        credentials = {"username": "admin-user", "password": "secret-password"}
        with mock.patch.object(api_client, "http_json", side_effect=self.bridge), mock.patch.object(
            cockpit, "run_project_creation", side_effect=self.fake_creation,
        ), mock.patch.object(
            cockpit, "create_wp_user", return_value=credentials,
        ), mock.patch.object(
            cockpit, "_autologin_installed", return_value=False,
        ):
            site = api_client.command_provision(provision_args)
        self.assertEqual(site["project"], "integration-site")
        self.assertEqual(site["username"], "admin-user")

        with mock.patch.object(api_client, "http_json", side_effect=self.bridge):
            status = api_client.command_status(status_args)
        self.assertEqual(status["active_sites"], 1)

        revoke_args = SimpleNamespace(config=self.config, yes=True)
        with mock.patch.object(api_client, "http_json", side_effect=self.bridge):
            revoked = api_client.command_revoke(revoke_args)
        self.assertEqual(revoked, {"status": "revoked", "active_sites": 1})
        self.assertFalse(self.config.exists())
        self.assertTrue((self.projects / "integration-site").is_dir())


if __name__ == "__main__":
    unittest.main()
