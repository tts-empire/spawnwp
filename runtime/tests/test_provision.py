import importlib
import json
import os
import secrets
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest import mock

RUNTIME = Path(__file__).parents[1]
sys.path.insert(0, str(RUNTIME))

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    import machine_auth
    HAS_RUNTIME = True
except Exception:
    HAS_RUNTIME = False


@unittest.skipUnless(HAS_RUNTIME, "requires cockpit runtime dependencies")
class ProvisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.import_temp = tempfile.TemporaryDirectory()
        static = Path(cls.import_temp.name)
        (static / "assets").mkdir()
        os.environ["SPAWNWP_STATIC_DIR"] = str(static)
        try:
            cls.cockpit = importlib.import_module("app")
            cls.provision = importlib.import_module("provision")
            cls.ingest = importlib.import_module("ingest")
        except Exception as exc:
            raise unittest.SkipTest(f"cockpit import failed: {exc}")

    @classmethod
    def tearDownClass(cls):
        cls.import_temp.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.projects = root / "srv"
        self.projects.mkdir()
        self.db_path = root / "ingest.db"
        os.environ.update(
            SPAWNWP_INGEST_DB=str(self.db_path),
            SPAWNWP_CONFIG=str(root / "config.env"),
            SPAWNWP_PROVISION_MAX_SITES_PER_CONNECTION="3",
        )
        self.old_projects_root = self.cockpit.PROJECTS_ROOT
        self.cockpit.PROJECTS_ROOT = self.projects
        self.keys = machine_auth.generate_keypair()
        self.connection_id = secrets.token_hex(16)
        db = self.ingest._connect()
        try:
            self.provision._initialize(db)
            now = int(time.time())
            db.execute(
                "INSERT INTO connections(id,public_key,status,scope,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?)",
                (self.connection_id, self.keys["public"], "active", "provision", now, now),
            )
            db.commit()
        finally:
            db.close()
        api = FastAPI()
        api.include_router(self.provision.router)
        self.client = TestClient(api)

    def tearDown(self):
        self.cockpit.PROJECTS_ROOT = self.old_projects_root
        self.temp.cleanup()

    def signed(self, payload: dict, key="request-0001"):
        body = json.dumps(payload, separators=(",", ":")).encode()
        return self.signed_request("POST", "/api/provision", body, {
            "Content-Type": "application/json",
            "Idempotency-Key": key,
        })

    def signed_request(self, method: str, path: str, body=b"", extra_headers=None):
        timestamp = int(time.time())
        nonce = secrets.token_hex(16)
        signature = machine_auth.sign(
            self.keys["private"], method, path, timestamp, nonce, body,
        )
        headers = {
            "X-SpawnWP-Connection": self.connection_id,
            "X-SpawnWP-Timestamp": str(timestamp),
            "X-SpawnWP-Nonce": nonce,
            "X-SpawnWP-Signature": signature,
        }
        headers.update(extra_headers or {})
        return self.client.request(
            method,
            path,
            content=body,
            headers=headers,
        )

    def fake_creation(self, site, timeout=300):
        project = self.projects / site.name
        project.mkdir()
        (project / "compose.yaml").write_text("services: {}\n")
        (project / "Makefile").write_text(":\n")
        (project / ".env").write_text(
            f"WP_HOME=https://example.test/{site.name}\n"
            f"SPAWNWP_EXPIRES={int(time.time()) + site.lifetime_seconds}\n"
        )
        return site.name

    def test_happy_path_is_idempotently_replayed(self):
        payload = {"blueprint": "development", "role": "editor"}
        credentials = {
            "username": "demo-editor",
            "password": "generated-password",
        }
        with mock.patch.object(
            self.cockpit, "run_project_creation", side_effect=self.fake_creation,
        ) as create, mock.patch.object(
            self.cockpit, "create_wp_user", return_value=credentials,
        ), mock.patch.object(
            self.cockpit, "_autologin_installed", return_value=False,
        ):
            first = self.signed(payload)
            second = self.signed(payload)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(second.headers["Idempotent-Replayed"], "true")
        create.assert_called_once()

    def test_ingest_scope_cannot_provision(self):
        db = self.ingest._connect()
        try:
            db.execute("UPDATE connections SET scope='ingest' WHERE id=?", (self.connection_id,))
            db.commit()
        finally:
            db.close()
        response = self.signed({"blueprint": "development"})
        self.assertEqual(response.status_code, 403)

    def test_invalid_role_is_rejected_before_creation(self):
        with mock.patch.object(self.cockpit, "run_project_creation") as create:
            response = self.signed({
                "blueprint": "development",
                "role": "super-admin",
            })
        self.assertEqual(response.status_code, 422, response.text)
        create.assert_not_called()

    def test_default_role_is_administrator(self):
        payload = self.provision.ProvisionRequest(blueprint="development")
        self.assertEqual(payload.role, "administrator")

    def test_creation_failure_reports_error_before_rollback_notice(self):
        process = mock.Mock()
        process.returncode = 1
        process.communicate.return_value = (
            "==> Importing captured database...\n"
            "Error: Unable to determine the source table prefix from the export.\n"
            "!! Creation failed; rolling back partial resources...\n",
            None,
        )
        stderr = StringIO()
        with mock.patch.object(
            self.cockpit, "prepare_new_project",
            return_value=("broken-site", ["false"], None),
        ), mock.patch.object(
            self.cockpit.subprocess, "Popen", return_value=process,
        ), redirect_stderr(stderr), self.assertRaises(HTTPException) as raised:
            self.cockpit.run_project_creation(mock.Mock(), timeout=10)
        self.assertEqual(raised.exception.status_code, 500)
        self.assertEqual(
            raised.exception.detail,
            "Error: Unable to determine the source table prefix from the export.",
        )
        self.assertIn("project=broken-site rc=1", stderr.getvalue())
        self.assertNotIn("rolling back", stderr.getvalue())

    def test_creation_failure_redacts_secrets(self):
        detail = self.cockpit.creation_failure_detail(
            "ERROR: upstream token=do-not-return\n"
            "!! Creation failed; rolling back partial resources...\n"
        )
        self.assertEqual(detail, "ERROR: upstream token=[redacted]")

    def test_status_reports_defaults_limits_and_sites(self):
        project = self.projects / "api-site"
        project.mkdir()
        (project / "compose.yaml").write_text("services: {}\n")
        (project / "Makefile").write_text(":\n")
        (project / ".env").write_text("WP_HOME=https://api-site.example.test\n")
        now = int(time.time())
        db = self.ingest._connect()
        try:
            db.execute(
                "INSERT INTO provision_sites(project,connection_id,status,expires_at,created_at) "
                "VALUES (?,?,?,?,?)",
                ("api-site", self.connection_id, "active", now + 3600, now),
            )
            db.commit()
        finally:
            db.close()

        response = self.signed_request("GET", "/api/provision/status")

        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["api_version"], 1)
        self.assertEqual(result["connection"]["scope"], "provision")
        self.assertEqual(result["defaults"]["role"], "administrator")
        self.assertEqual(result["limits"]["concurrent_sites"], 3)
        self.assertEqual(result["active_sites"], 1)
        self.assertEqual(result["sites"][0]["url"], "https://api-site.example.test")

    def test_status_discards_sites_that_no_longer_exist(self):
        now = int(time.time())
        db = self.ingest._connect()
        try:
            db.execute(
                "INSERT INTO provision_sites(project,connection_id,status,expires_at,created_at) "
                "VALUES (?,?,?,?,?)",
                ("missing-site", self.connection_id, "active", now + 3600, now),
            )
            db.commit()
        finally:
            db.close()

        response = self.signed_request("GET", "/api/provision/status")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["active_sites"], 0)

    def test_revoke_invalidates_key_but_preserves_active_site(self):
        project = self.projects / "surviving-site"
        project.mkdir()
        (project / "compose.yaml").write_text("services: {}\n")
        (project / "Makefile").write_text(":\n")
        now = int(time.time())
        db = self.ingest._connect()
        try:
            db.execute(
                "INSERT INTO provision_sites(project,connection_id,status,expires_at,created_at) "
                "VALUES (?,?,?,?,?)",
                ("surviving-site", self.connection_id, "active", now + 3600, now),
            )
            db.execute(
                "INSERT INTO provision_requests(connection_id,idempotency_key,request_hash,state,"
                "project,response_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (self.connection_id, "request-old1", "hash", "complete",
                 "surviving-site", '{"password":"secret"}', now, now),
            )
            db.commit()
        finally:
            db.close()

        response = self.signed_request("DELETE", "/api/provision/connection")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"status": "revoked", "active_sites": 1})
        db = self.ingest._connect()
        try:
            connection = db.execute(
                "SELECT status,public_key FROM connections WHERE id=?",
                (self.connection_id,),
            ).fetchone()
            sites = db.execute(
                "SELECT COUNT(*) FROM provision_sites WHERE connection_id=?",
                (self.connection_id,),
            ).fetchone()[0]
            requests = db.execute(
                "SELECT COUNT(*) FROM provision_requests WHERE connection_id=?",
                (self.connection_id,),
            ).fetchone()[0]
        finally:
            db.close()
        self.assertEqual(connection["status"], "revoked")
        self.assertEqual(connection["public_key"], "")
        self.assertEqual(sites, 1)
        self.assertEqual(requests, 0)
        rejected = self.signed_request("GET", "/api/provision/status")
        self.assertEqual(rejected.status_code, 401)

    def test_post_create_failure_runs_compensating_cleanup(self):
        with mock.patch.object(
            self.cockpit, "run_project_creation", side_effect=self.fake_creation,
        ), mock.patch.object(
            self.cockpit, "create_wp_user",
            side_effect=HTTPException(400, "WordPress rejected the user"),
        ), mock.patch.object(
            self.cockpit, "cleanup_created_project", return_value=True,
        ) as cleanup:
            response = self.signed({"blueprint": "development"})
        self.assertEqual(response.status_code, 400, response.text)
        cleanup.assert_called_once()
        db = self.ingest._connect()
        try:
            count = db.execute("SELECT COUNT(*) FROM provision_sites").fetchone()[0]
        finally:
            db.close()
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
