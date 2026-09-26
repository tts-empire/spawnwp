import asyncio
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi import HTTPException

RUNTIME = Path(__file__).parents[1]
sys.path.insert(0, str(RUNTIME))

import automation


class Request:
    def __init__(self, request_id):
        self.headers = {"Idempotency-Key": request_id}


class Cockpit:
    PROJECTS_ROOT = Path("/nonexistent")

    @staticmethod
    def random_project_name(prefix):
        return prefix + "-test-project"

    @staticmethod
    def is_project(_path):
        return False


class AutomationTests(unittest.TestCase):
    connection = {"id": "connection-1", "module_id": "spawnwp-mcp"}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.old = {key: os.environ.get(key) for key in (
            "SPAWNWP_AUTOMATION_DB", "SPAWNWP_OPERATION_LOCK",
        )}
        os.environ["SPAWNWP_AUTOMATION_DB"] = str(root / "automation.sqlite3")
        os.environ["SPAWNWP_OPERATION_LOCK"] = str(root / "operation.lock")
        with automation.database() as db:
            db.execute(
                "INSERT INTO grants(id,connection_id,subject,label,status,actions,projects,quota,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                ("a" * 32, self.connection["id"], "client-01", "Test client", "active",
                 json.dumps(["read", "create", "stop"]), "{}", 3, int(time.time())),
            )
        self.machine = mock.AsyncMock(return_value=(self.connection, b""))
        self.active = mock.patch.object(automation, "connection_active", return_value=self.connection)
        self.capabilities = mock.patch.object(automation, "capabilities", return_value={"read", "create", "stop"})
        self.authorize = mock.patch.object(automation, "authorize_project")
        self.cockpit = mock.patch.object(automation, "cockpit", return_value=Cockpit())
        self.machine_patch = mock.patch.object(automation, "machine", self.machine)
        self.active.start()
        self.capabilities.start()
        self.authorize.start()
        self.cockpit.start()
        self.machine_patch.start()

    def tearDown(self):
        self.machine_patch.stop()
        self.authorize.stop()
        self.cockpit.stop()
        self.capabilities.stop()
        self.active.stop()
        for key, value in self.old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.temp.cleanup()

    def submit(self, value):
        request_id = value["request_id"]
        self.machine.return_value = self.connection, json.dumps(value).encode()
        return asyncio.run(automation.submit(Request(request_id)))

    def test_stop_is_queued_for_explicit_approval(self):
        result = self.submit({
            "grant_id": "a" * 32, "request_id": "request-1", "action": "stop",
            "project": "demo-site",
        })
        self.assertEqual(result["status"], "awaiting_approval")
        self.assertEqual(result["approval_url"], "/automations")

    def test_same_idempotency_key_returns_the_original_operation(self):
        body = {
            "grant_id": "a" * 32, "request_id": "request-2", "action": "create",
            "blueprint": "wordpress",
        }
        first = self.submit(body)
        second = self.submit(body)
        self.assertEqual(second["operation_id"], first["operation_id"])
        changed = {**body, "blueprint": "woocommerce"}
        with self.assertRaises(HTTPException) as raised:
            self.submit(changed)
        self.assertEqual(raised.exception.status_code, 409)

    def test_revocation_cancels_not_yet_started_operations(self):
        result = self.submit({
            "grant_id": "a" * 32, "request_id": "request-3", "action": "create",
            "blueprint": "wordpress",
        })
        with automation.database() as db:
            automation._revoke(db, "a" * 32)
            row = db.execute("SELECT status FROM operations WHERE id=?", (result["operation_id"],)).fetchone()
        self.assertEqual(row["status"], "cancelled")

    def test_worker_restart_marks_running_operations_interrupted(self):
        with automation.database() as db:
            db.execute(
                "INSERT INTO operations(id,grant_id,request_id,request_hash,payload,status,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                ("b" * 32, "a" * 32, "restart-1", "hash", "{}", "running", 1, 1),
            )
        automation.start_worker()
        try:
            deadline = time.time() + 3
            while time.time() < deadline:
                with automation.database() as db:
                    row = db.execute("SELECT status FROM operations WHERE id=?", ("b" * 32,)).fetchone()
                if row["status"] == "interrupted":
                    break
                time.sleep(0.02)
            self.assertEqual(row["status"], "interrupted")
        finally:
            automation.stop_worker()


if __name__ == "__main__":
    unittest.main()
