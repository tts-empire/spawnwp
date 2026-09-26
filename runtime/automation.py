"""Core-owned grants and durable, idempotent, least-privilege operations.

Only admin routes (session + CSRF + recent authentication in app.py) can grant
access or approve operations. Machine routes never accept a caller's permissions.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import signal
import sqlite3
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

import ingest
import machine_auth
import module_api
import operation_lock

router = APIRouter()
ACTIONS = {"read", "create", "start", "stop", "expiry", "snapshot"}
SLUG = r"^[a-z0-9][a-z0-9-]{0,30}$"
KEY = r"^[A-Za-z0-9._:-]{8,128}$"
_stop = threading.Event()
_worker: threading.Thread | None = None


def cockpit():
    import app
    return app


@contextmanager
def database():
    path = Path(os.environ.get("SPAWNWP_AUTOMATION_DB", "/var/lib/spawnwp/automation.sqlite3"))
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(path, timeout=20)
    db.row_factory = sqlite3.Row
    os.chmod(path, 0o600)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS grants(
            id TEXT PRIMARY KEY, connection_id TEXT NOT NULL, subject TEXT NOT NULL,
            label TEXT NOT NULL, status TEXT NOT NULL, actions TEXT NOT NULL DEFAULT '[]',
            projects TEXT NOT NULL DEFAULT '{}', quota INTEGER NOT NULL DEFAULT 3,
            created_at INTEGER NOT NULL, UNIQUE(connection_id, subject));
        CREATE TABLE IF NOT EXISTS operations(
            id TEXT PRIMARY KEY, grant_id TEXT NOT NULL, request_id TEXT NOT NULL,
            request_hash TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL,
            result TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
            approval_expires INTEGER NOT NULL DEFAULT 0, baseline TEXT NOT NULL DEFAULT '',
            UNIQUE(grant_id, request_id));
        CREATE TABLE IF NOT EXISTS owned_projects(
            project TEXT PRIMARY KEY, grant_id TEXT NOT NULL, fingerprint TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audit(
            id INTEGER PRIMARY KEY, at INTEGER NOT NULL, grant_id TEXT NOT NULL,
            operation_id TEXT NOT NULL, event TEXT NOT NULL);
    """)
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def audit(db, grant_id, operation_id, event):
    db.execute("INSERT INTO audit(at,grant_id,operation_id,event) VALUES (?,?,?,?)",
               (int(time.time()), grant_id, operation_id, event))


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class GrantRequest(Strict):
    subject: str = Field(pattern=KEY)
    label: str = Field(min_length=1, max_length=80)


class GrantApproval(Strict):
    actions: list[Literal["read", "create", "start", "stop", "expiry", "snapshot"]] = ["read"]
    projects: list[str] = Field(default_factory=list, max_length=100)
    quota: int = Field(default=3, ge=1, le=100)


class ReadRequest(Strict):
    grant_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    resource: Literal["projects", "project", "blueprints", "capacity", "snapshots"]
    project: str | None = Field(default=None, pattern=SLUG)


class Operation(Strict):
    grant_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    request_id: str = Field(pattern=KEY)
    action: Literal["create", "start", "stop", "expiry", "snapshot"]
    project: str | None = Field(default=None, pattern=SLUG)
    blueprint: str | None = Field(default=None, pattern=SLUG)
    expires_seconds: int | None = Field(default=None, ge=300, le=31536000)


async def machine(request: Request):
    db = ingest._connect()
    try:
        connection, body = await machine_auth.authorize(request, db, "automation")
        if connection["connection_kind"] != "local_module":
            raise HTTPException(403, "A signed local automation module is required")
        return dict(connection), body
    finally:
        db.close()


def parse(model, body):
    try:
        return model.model_validate_json(body)
    except ValidationError as exc:
        # Do not reflect arbitrary input into a response or an LLM's context.
        raise HTTPException(422, "Invalid automation request") from exc


def capabilities(connection):
    manifest = module_api._manifest(connection["module_id"])
    if manifest.get("core_api_scope") != "automation" or manifest.get("schema") != 2:
        raise HTTPException(403, "Installed module is not an automation module")
    return set(manifest.get("core_capabilities", [])) & ACTIONS


def connection_active(connection_id):
    db = ingest._connect()
    try:
        row = db.execute("SELECT * FROM connections WHERE id=? AND status='active' AND scope='automation' AND connection_kind='local_module'", (connection_id,)).fetchone()
        return dict(row) if row else None
    finally:
        db.close()


def grant(db, grant_id, connection_id=None):
    row = db.execute("SELECT * FROM grants WHERE id=?", (grant_id,)).fetchone()
    if not row or (connection_id and row["connection_id"] != connection_id):
        raise HTTPException(404, "Connection authorization not found")
    if row["status"] != "active":
        raise HTTPException(403, "Connection authorization is not active")
    connection = connection_active(row["connection_id"])
    if not connection:
        raise HTTPException(403, "Module access has been revoked")
    allowed = set(json.loads(row["actions"])) & capabilities(connection)
    return row, allowed


def fingerprint(name):
    api = cockpit()
    if not re.fullmatch(SLUG, name):
        raise HTTPException(400, "Invalid project")
    path = api.resolve_project(name)
    if path.resolve().parent != api.PROJECTS_ROOT.resolve() or path.is_symlink():
        raise HTTPException(403, "Unsafe project location")
    st = path.stat()
    return f"{st.st_dev}:{st.st_ino}"


def permitted_projects(db, row):
    result = json.loads(row["projects"])
    for owned in db.execute("SELECT * FROM owned_projects WHERE grant_id=?", (row["id"],)):
        result[owned["project"]] = owned["fingerprint"]
    valid = []
    for name, expected in result.items():
        try:
            if fingerprint(name) == expected:
                valid.append(name)
        except (HTTPException, OSError):
            pass
    return valid


def authorize_project(db, row, name, *, write=False):
    if not name or name not in permitted_projects(db, row):
        raise HTTPException(403, "Project is outside this connection's authorization")
    if write and cockpit().resolve_project(name).resolve() == cockpit().PRIMARY_PROJECT.resolve():
        raise HTTPException(403, "Automation cannot change the primary environment")


def public_operation(row):
    return {key: row[key] for key in ("id", "grant_id", "status", "created_at", "updated_at", "error")} | {
        "operation_id": row["id"], "request": json.loads(row["payload"]), "result": json.loads(row["result"]),
        "approval_url": "/automations" if row["status"] == "awaiting_approval" else None,
    }


@router.get("/api/automation/v1/capabilities")
async def machine_capabilities(request: Request):
    connection, _ = await machine(request)
    return {"version": 1, "actions": sorted(capabilities(connection))}


@router.post("/api/automation/v1/grants", status_code=202)
async def request_grant(request: Request):
    connection, body = await machine(request)
    value = parse(GrantRequest, body)
    capabilities(connection)
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute("SELECT id,status FROM grants WHERE connection_id=? AND subject=?", (connection["id"], value.subject)).fetchone()
        if existing:
            return dict(existing)
        count = db.execute("SELECT COUNT(*) FROM grants WHERE connection_id=? AND status='pending'", (connection["id"],)).fetchone()[0]
        if count >= 20:
            raise HTTPException(429, "Too many pending connection requests")
        gid = secrets.token_hex(16)
        db.execute("INSERT INTO grants(id,connection_id,subject,label,status,created_at) VALUES (?,?,?,?,?,?)",
                   (gid, connection["id"], value.subject, value.label, "pending", int(time.time())))
        audit(db, gid, "", "grant_requested")
        return {"id": gid, "status": "pending", "approval_url": "/automations"}


@router.get("/api/automation/v1/grants/{grant_id}")
async def grant_status(grant_id: str, request: Request):
    connection, _ = await machine(request)
    with database() as db:
        row = db.execute("SELECT id,status,actions FROM grants WHERE id=? AND connection_id=?", (grant_id, connection["id"])).fetchone()
        if not row:
            raise HTTPException(404, "Authorization not found")
        return {"id": row["id"], "status": row["status"], "actions": json.loads(row["actions"])}


@router.delete("/api/automation/v1/grants/{grant_id}")
async def machine_revoke(grant_id: str, request: Request):
    connection, _ = await machine(request)
    with database() as db:
        if not db.execute("SELECT 1 FROM grants WHERE id=? AND connection_id=?", (grant_id, connection["id"])).fetchone():
            raise HTTPException(404, "Authorization not found")
        _revoke(db, grant_id)
    return {"revoked": True}


@router.post("/api/automation/v1/read")
async def machine_read(request: Request):
    connection, body = await machine(request)
    value = parse(ReadRequest, body)
    def read():
        with database() as db:
            row, allowed = grant(db, value.grant_id, connection["id"])
            if "read" not in allowed:
                raise HTTPException(403, "Read permission is required")
            api = cockpit()
            if value.resource == "blueprints":
                return api.blueprint_catalog()
            if value.resource == "capacity":
                return api.public_capacity_snapshot(api.PROJECTS_ROOT)
            if value.resource in {"project", "snapshots"}:
                authorize_project(db, row, value.project)
            if value.resource == "snapshots":
                return {"snapshots": api.list_snapshots(value.project), "coverage": ["database", "uploads"]}
            names = permitted_projects(db, row)
            # Allowlist the output fields, never forward .env or credentials.
            fields = {"name", "url", "blueprint", "expires_at", "php", "containers"}
            projects = [{key: item[key] for key in fields if key in item} for item in api.list_projects()
                        if item["name"] in names and (not value.project or item["name"] == value.project)]
            return {"projects": projects}
    return await asyncio.to_thread(read)


def expiry_baseline(project):
    return cockpit()._read_env(cockpit().resolve_project(project)).get("SPAWNWP_EXPIRES", "")


@router.post("/api/automation/v1/operations", status_code=202)
async def submit(request: Request):
    connection, body = await machine(request)
    value = parse(Operation, body)
    if request.headers.get("Idempotency-Key") != value.request_id:
        raise HTTPException(400, "Idempotency-Key must match the signed request_id")
    if value.action == "create":
        if not value.blueprint:
            raise HTTPException(422, "Blueprint is required")
    elif not value.project or value.blueprint is not None or (value.action != "expiry" and value.expires_seconds is not None):
        raise HTTPException(422, "Arguments do not match the operation")
    digest = hashlib.sha256(json.dumps(value.model_dump(), sort_keys=True).encode()).hexdigest()
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        row, allowed = grant(db, value.grant_id, connection["id"])
        if value.action not in allowed:
            raise HTTPException(403, "Operation not authorized")
        old = db.execute("SELECT * FROM operations WHERE grant_id=? AND request_id=?", (row["id"], value.request_id)).fetchone()
        if old:
            if old["request_hash"] != digest:
                raise HTTPException(409, "Idempotency key has already been used with different arguments")
            return public_operation(old)
        if db.execute("SELECT COUNT(*) FROM operations WHERE status IN ('queued','running','awaiting_approval')").fetchone()[0] >= 100:
            raise HTTPException(429, "Operation queue is full")
        data = value.model_dump()
        baseline = ""
        if value.action == "create":
            occupied = db.execute("SELECT COUNT(*) FROM operations WHERE grant_id=? AND status IN ('queued','running') AND json_extract(payload,'$.action')='create'", (row["id"],)).fetchone()[0]
            owned = [p for p in db.execute("SELECT project FROM owned_projects WHERE grant_id=?", (row["id"],)) if cockpit().is_project(cockpit().PROJECTS_ROOT / p[0])]
            if len(owned) + occupied >= row["quota"]:
                raise HTTPException(409, "Connection project quota reached")
            # Generated names are persisted before dispatch; retries never create a new name.
            data["project"] = value.project or cockpit().random_project_name("mcp")
            if cockpit().is_project(cockpit().PROJECTS_ROOT / data["project"]):
                raise HTTPException(409, "Project already exists")
        else:
            authorize_project(db, row, value.project, write=True)
        now = int(time.time())
        needs_approval = value.action == "stop"
        if value.action == "expiry":
            baseline = expiry_baseline(value.project)
            data["expires_at"] = now + value.expires_seconds if value.expires_seconds else None
            needs_approval = data["expires_at"] is not None and (not baseline.isdigit() or data["expires_at"] < int(baseline))
        oid = secrets.token_hex(16)
        db.execute("INSERT INTO operations(id,grant_id,request_id,request_hash,payload,status,created_at,updated_at,baseline) VALUES (?,?,?,?,?,?,?,?,?)",
                   (oid, row["id"], value.request_id, digest, json.dumps(data), "awaiting_approval" if needs_approval else "queued", now, now, baseline))
        audit(db, row["id"], oid, "submitted")
        return public_operation(db.execute("SELECT * FROM operations WHERE id=?", (oid,)).fetchone())


@router.get("/api/automation/v1/grants/{grant_id}/operations/{operation_id}")
async def operation_status(grant_id: str, operation_id: str, request: Request):
    connection, _ = await machine(request)
    with database() as db:
        grant(db, grant_id, connection["id"])
        row = db.execute("SELECT * FROM operations WHERE id=? AND grant_id=?", (operation_id, grant_id)).fetchone()
        if not row:
            raise HTTPException(404, "Operation not found")
        return public_operation(row)


@router.get("/api/automation-admin")
def admin_state():
    with database() as db:
        grants = [{**dict(row), "actions": json.loads(row["actions"]), "projects": list(json.loads(row["projects"]))} for row in db.execute("SELECT * FROM grants ORDER BY created_at DESC LIMIT 200")]
        operations = [public_operation(row) for row in db.execute("SELECT * FROM operations ORDER BY created_at DESC LIMIT 200")]
        return {"grants": grants, "operations": operations,
                "projects": [p.name for p in cockpit().get_projects()],
                "audit": [dict(row) for row in db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 200")]}


@router.post("/api/automation-admin/grants/{grant_id}/approve")
def approve_grant(grant_id: str, value: GrantApproval):
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM grants WHERE id=? AND status='pending'", (grant_id,)).fetchone()
        if not row:
            raise HTTPException(409, "Only pending connections can be approved; revoke and reconnect to change permissions")
        connection = connection_active(row["connection_id"])
        if not connection or not set(value.actions) <= capabilities(connection) or "read" not in value.actions:
            raise HTTPException(403, "Permissions exceed the signed module capabilities")
        projects = {name: fingerprint(name) for name in value.projects}
        db.execute("UPDATE grants SET status='active',actions=?,projects=?,quota=? WHERE id=?",
                   (json.dumps(sorted(set(value.actions))), json.dumps(projects), value.quota, grant_id))
        audit(db, grant_id, "", "approved")
    return {"approved": True}


def _revoke(db, grant_id):
    db.execute("UPDATE grants SET status='revoked' WHERE id=?", (grant_id,))
    db.execute("UPDATE operations SET status='cancelled',updated_at=? WHERE grant_id=? AND status IN ('queued','awaiting_approval')", (int(time.time()), grant_id))
    audit(db, grant_id, "", "revoked")


def revoke_module(connection_id):
    with database() as db:
        for row in db.execute("SELECT id FROM grants WHERE connection_id=?", (connection_id,)).fetchall():
            _revoke(db, row[0])


@router.delete("/api/automation-admin/grants/{grant_id}")
def revoke_grant(grant_id: str):
    with database() as db:
        _revoke(db, grant_id)
    return {"revoked": True}


@router.post("/api/automation-admin/operations/{operation_id}/approve")
def approve_operation(operation_id: str):
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM operations WHERE id=? AND status='awaiting_approval'", (operation_id,)).fetchone()
        if not row:
            raise HTTPException(409, "Operation is not awaiting approval")
        g, _ = grant(db, row["grant_id"])
        data = json.loads(row["payload"])
        authorize_project(db, g, data["project"], write=True)
        baseline = expiry_baseline(data["project"]) if data["action"] == "expiry" else ""
        now = int(time.time())
        db.execute("UPDATE operations SET status='queued',approval_expires=?,baseline=?,updated_at=? WHERE id=?", (now + 600, baseline, now, operation_id))
        audit(db, row["grant_id"], operation_id, "approved")
    return {"approved": True}


@router.delete("/api/automation-admin/operations/{operation_id}")
def cancel_operation(operation_id: str):
    with database() as db:
        changed = db.execute("UPDATE operations SET status='cancelled',updated_at=? WHERE id=? AND status IN ('queued','awaiting_approval')", (int(time.time()), operation_id)).rowcount
        if not changed:
            raise HTTPException(409, "Only pending operations can be cancelled")
    return {"cancelled": True}


def execute(data):
    api = cockpit()
    api.guard_not_busy()
    action, project = data["action"], data["project"]
    if action == "create":
        name = api.run_project_creation(api.NewProject(name=project, blueprint=data["blueprint"], lifetime_seconds=data["expires_seconds"]), timeout=600)
        return {"project": name}
    path = api.resolve_project(project)
    if action == "expiry":
        expires_at = data["expires_at"]
        seconds = expires_at - int(time.time()) if expires_at else None
        if seconds is not None and seconds < 300:
            raise HTTPException(409, "Requested expiry is too close or has passed; submit a new request")
        api.set_project_lifetime(path, seconds, 0)
        return {"project": project, "expires_at": expires_at}
    reservation = api.reserve_start(path) if action == "start" else None
    command = ["make", "-s", {"start": "up", "stop": "down", "snapshot": "snapshot"}[action]]
    if action == "snapshot":
        command.append("INCLUDE_FILES=1")
    try:
        # Never capture or return potentially secret command output.
        process = subprocess.Popen(command, cwd=path, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, **operation_lock.child_options())
        try:
            code = process.wait(timeout=600)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            raise HTTPException(504, "Operation timed out; inspect the environment before retrying")
        if code:
            raise HTTPException(500, "Operation failed; inspect the cockpit")
        return {"project": project, "action": action}
    finally:
        api.release_reservation(reservation)


def run_one():
    try:
        lock = operation_lock.acquire()
    except HTTPException:
        return False
    token = operation_lock.current.set(lock)
    try:
        with database() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM operations WHERE status='queued' ORDER BY created_at,id LIMIT 1").fetchone()
            if not row:
                return False
            data = json.loads(row["payload"])
            try:
                g, allowed = grant(db, row["grant_id"])
                if data["action"] not in allowed:
                    raise HTTPException(403, "Operation no longer authorized")
                if data["action"] != "create":
                    authorize_project(db, g, data["project"], write=True)
                now = int(time.time())
                approval_needed = data["action"] == "stop"
                if data["action"] == "expiry":
                    baseline = expiry_baseline(data["project"])
                    approval_needed = data["expires_at"] is not None and (not baseline.isdigit() or data["expires_at"] < int(baseline))
                    if approval_needed and baseline != row["baseline"]:
                        db.execute("UPDATE operations SET approval_expires=0 WHERE id=?", (row["id"],))
                        approval_needed = True
                        row = dict(row) | {"approval_expires": 0}
                if approval_needed and row["approval_expires"] < now:
                    db.execute("UPDATE operations SET status='awaiting_approval',updated_at=? WHERE id=?", (now, row["id"]))
                    return True
                db.execute("UPDATE operations SET status='running',updated_at=? WHERE id=?", (now, row["id"]))
                audit(db, row["grant_id"], row["id"], "started")
            except (HTTPException, module_api.ModuleAPIError):
                db.execute("UPDATE operations SET status='cancelled',error='Authorization is no longer valid',updated_at=? WHERE id=?", (int(time.time()), row["id"]))
                return True
        try:
            result = execute(data)
            state, error = "succeeded", ""
        except HTTPException as exc:
            result, state, error = {}, "failed", {403: "not_authorized", 409: "conflict_or_capacity", 507: "insufficient_disk", 503: "unavailable", 504: "timeout"}.get(exc.status_code, "operation_failed")
        except Exception:
            result, state, error = {}, "failed", "operation_failed"
        with database() as db:
            if state == "succeeded" and data["action"] == "create":
                db.execute("INSERT OR REPLACE INTO owned_projects(project,grant_id,fingerprint) VALUES (?,?,?)", (data["project"], row["grant_id"], fingerprint(data["project"])))
            db.execute("UPDATE operations SET status=?,result=?,error=?,updated_at=? WHERE id=?", (state, json.dumps(result), error, int(time.time()), row["id"]))
            audit(db, row["grant_id"], row["id"], state)
        return True
    finally:
        operation_lock.current.reset(token)
        operation_lock.release(lock)


def start_worker():
    global _worker
    if _worker and _worker.is_alive():
        return
    _stop.clear()
    def loop():
        # A worker singleton lock prevents multi-worker ASGI startup from treating
        # another process's active jobs as crashed.
        import fcntl
        path = Path(os.environ.get("SPAWNWP_AUTOMATION_DB", "/var/lib/spawnwp/automation.sqlite3"))
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with path.with_suffix(".worker.lock").open("a") as singleton:
            try:
                fcntl.flock(singleton, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            with database() as db:
                # Never replay an uncertain create/snapshot after a crash.
                db.execute("UPDATE operations SET status='interrupted',error='Worker restarted; inspect the environment before submitting a new operation',updated_at=? WHERE status='running'", (int(time.time()),))
            while not _stop.is_set():
                try:
                    if not run_one():
                        _stop.wait(1)
                except Exception:
                    _stop.wait(2)
    _worker = threading.Thread(target=loop, name="spawnwp-automation", daemon=True)
    _worker.start()


def stop_worker():
    _stop.set()
