"""Signed, idempotent temporary-site provisioning for trusted integrators."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

import app as cockpit
import ingest
import machine_auth

router = APIRouter()

IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
DEFAULT_CONNECTION_QUOTA = 3
PROVISION_TIMEOUT_SECONDS = 300
API_VERSION = 1


class ProvisionRequest(BaseModel):
    blueprint: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,30}$")
    expires_seconds: int = Field(default=3600, ge=300, le=365 * 86400)
    role: Literal["administrator", "editor", "author", "contributor", "subscriber"] = "administrator"
    group: str = Field(default="API", pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._\-]{0,31}$")
    name: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{0,30}$")
    access_profile: Literal["standard", "restricted-admin"] = "standard"
    credentials_mode: Literal["return", "managed"] = "return"


def _initialize(db: sqlite3.Connection) -> None:
    db.executescript("""
        CREATE TABLE IF NOT EXISTS provision_requests(
            connection_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            state TEXT NOT NULL,
            project TEXT NOT NULL,
            response_json TEXT NOT NULL DEFAULT '',
            error TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY(connection_id, idempotency_key)
        );
        CREATE TABLE IF NOT EXISTS provision_sites(
            project TEXT PRIMARY KEY,
            connection_id TEXT NOT NULL,
            status TEXT NOT NULL,
            expires_at INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            username TEXT NOT NULL DEFAULT '',
            credentials_mode TEXT NOT NULL DEFAULT 'return'
        );
    """)
    columns = {row["name"] for row in db.execute("PRAGMA table_info(provision_sites)")}
    if "username" not in columns:
        db.execute("ALTER TABLE provision_sites ADD COLUMN username TEXT NOT NULL DEFAULT ''")
    if "credentials_mode" not in columns:
        db.execute(
            "ALTER TABLE provision_sites ADD COLUMN credentials_mode TEXT NOT NULL DEFAULT 'return'",
        )
    db.commit()


def _config_value(key: str, default: str) -> str:
    if key in os.environ:
        return os.environ[key]
    path = Path(os.environ.get("SPAWNWP_CONFIG", "/etc/spawnwp/config.env"))
    try:
        for line in path.read_text().splitlines():
            if line.startswith(f"{key}="):
                return line.partition("=")[2].strip()
    except OSError:
        pass
    return default


def _connection_quota() -> int:
    raw = _config_value(
        "SPAWNWP_PROVISION_MAX_SITES_PER_CONNECTION",
        str(DEFAULT_CONNECTION_QUOTA),
    )
    try:
        quota = int(raw)
    except ValueError as exc:
        raise HTTPException(
            500, "Invalid SPAWNWP_PROVISION_MAX_SITES_PER_CONNECTION configuration",
        ) from exc
    if not 1 <= quota <= 100:
        raise HTTPException(
            500, "SPAWNWP_PROVISION_MAX_SITES_PER_CONNECTION must be between 1 and 100",
        )
    return quota


def _discard_missing_sites(db: sqlite3.Connection) -> None:
    rows = db.execute("SELECT project FROM provision_sites").fetchall()
    missing = [
        row["project"] for row in rows
        if not cockpit.is_project(cockpit.PROJECTS_ROOT / row["project"])
    ]
    db.executemany("DELETE FROM provision_sites WHERE project=?", [(name,) for name in missing])


def _site_summary(row: sqlite3.Row) -> dict:
    project = row["project"]
    env = cockpit._read_env(cockpit.PROJECTS_ROOT / project)
    return {
        "project": project,
        "url": env.get("WP_HOME", ""),
        "status": row["status"],
        "expires_at": row["expires_at"],
        "created_at": row["created_at"],
    }


def _reserve(db: sqlite3.Connection, connection_id: str, key: str,
             request_hash: str, payload: ProvisionRequest) -> tuple[str, dict | None]:
    """Reserve quota and idempotency atomically; return name or replay payload."""
    db.execute("BEGIN IMMEDIATE")
    try:
        existing = db.execute(
            "SELECT * FROM provision_requests WHERE connection_id=? AND idempotency_key=?",
            (connection_id, key),
        ).fetchone()
        if existing:
            if existing["request_hash"] != request_hash:
                raise HTTPException(409, "Idempotency-Key was already used with a different body")
            if existing["state"] == "complete":
                db.commit()
                return existing["project"], json.loads(existing["response_json"])
            if existing["state"] == "in_progress":
                raise HTTPException(409, "This idempotent request is still in progress")
            db.execute(
                "DELETE FROM provision_requests WHERE connection_id=? AND idempotency_key=?",
                (connection_id, key),
            )

        _discard_missing_sites(db)
        active = db.execute(
            "SELECT COUNT(*) FROM provision_sites WHERE connection_id=?",
            (connection_id,),
        ).fetchone()[0]
        quota = _connection_quota()
        if active >= quota:
            raise HTTPException(409, f"Provisioning quota reached: {quota} concurrent sites")

        if payload.name and cockpit.is_project(cockpit.PROJECTS_ROOT / payload.name):
            raise HTTPException(409, f"Project '{payload.name}' already exists")
        project = payload.name or cockpit.random_project_name()
        now = int(time.time())
        db.execute(
            "INSERT INTO provision_requests(connection_id,idempotency_key,request_hash,state,"
            "project,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
            (connection_id, key, request_hash, "in_progress", project, now, now),
        )
        db.execute(
            "INSERT INTO provision_sites(project,connection_id,status,expires_at,created_at,"
            "credentials_mode) VALUES (?,?,?,?,?,?)",
            (project, connection_id, "provisioning", now + payload.expires_seconds, now,
             payload.credentials_mode),
        )
        db.commit()
        return project, None
    except Exception:
        db.rollback()
        raise


def _finish(connection_id: str, key: str, project: str, username: str,
            response: dict) -> None:
    db = ingest._connect()
    try:
        _initialize(db)
        now = int(time.time())
        db.execute(
            "UPDATE provision_requests SET state='complete',response_json=?,updated_at=? "
            "WHERE connection_id=? AND idempotency_key=?",
            (json.dumps(response, separators=(",", ":")), now, connection_id, key),
        )
        db.execute(
            "UPDATE provision_sites SET status='active',expires_at=?,username=? WHERE project=?",
            (response["expires_at"], username, project),
        )
        db.commit()
    finally:
        db.close()


def _fail(connection_id: str, key: str, project: str, detail: str,
          cleaned: bool) -> None:
    db = ingest._connect()
    try:
        _initialize(db)
        now = int(time.time())
        db.execute(
            "UPDATE provision_requests SET state='failed',error=?,updated_at=? "
            "WHERE connection_id=? AND idempotency_key=?",
            (detail[:500], now, connection_id, key),
        )
        if cleaned:
            db.execute("DELETE FROM provision_sites WHERE project=?", (project,))
        else:
            db.execute(
                "UPDATE provision_sites SET status='cleanup_required' WHERE project=?",
                (project,),
            )
        db.commit()
    finally:
        db.close()


def _execute_reserved(connection_id: str, key: str, project: str,
                      payload: ProvisionRequest) -> dict:
    """Run the blocking create/WP-CLI flow outside the ASGI event loop."""
    try:
        site = cockpit.NewProject(
            name=project,
            blueprint=payload.blueprint,
            lifetime_seconds=payload.expires_seconds,
            group=payload.group,
        )
        cockpit.run_project_creation(site, timeout=PROVISION_TIMEOUT_SECONDS)
        credentials = cockpit.create_wp_user(
            project,
            cockpit.WpUserCreate(role=payload.role),
        )
        proj = cockpit.resolve_project(project)
        if payload.credentials_mode == "managed" and not cockpit._autologin_installed(proj):
            cockpit.install_autologin(proj)
        if payload.access_profile == "restricted-admin":
            cockpit.install_restricted_admin(proj=proj)
        # The customer receives the complete requested lifetime; image builds and
        # post-create setup do not consume part of a managed demo's hour.
        cockpit.set_project_lifetime(proj, payload.expires_seconds)
        env = cockpit._read_env(proj)
        expires_at = int(env.get("SPAWNWP_EXPIRES", "0"))
        response = {
            "project": project,
            "url": env.get("WP_HOME", ""),
            "expires_at": expires_at,
            "username": credentials["username"],
        }
        if payload.credentials_mode == "return":
            response["password"] = credentials["password"]
        if payload.credentials_mode == "return" and cockpit._autologin_installed(proj):
            response["magic_link"] = cockpit.mint_magic_login(
                proj, credentials["username"],
            )["url"]
        _finish(connection_id, key, project, credentials["username"], response)
        return response
    except HTTPException as exc:
        cleaned = cockpit.cleanup_created_project(project)
        _fail(connection_id, key, project, str(exc.detail), cleaned)
        if not cleaned:
            raise HTTPException(
                exc.status_code,
                {"detail": exc.detail, "project": project, "status": "cleanup_required"},
            ) from exc
        raise
    except Exception as exc:
        cleaned = cockpit.cleanup_created_project(project)
        _fail(connection_id, key, project, str(exc), cleaned)
        detail: dict | str = "Provisioning failed"
        if not cleaned:
            detail = {
                "detail": "Provisioning failed",
                "project": project,
                "status": "cleanup_required",
            }
        raise HTTPException(500, detail) from exc


@router.post("/api/provision")
async def provision(request: Request):
    key = request.headers.get("idempotency-key", "")
    if not IDEMPOTENCY_RE.fullmatch(key):
        raise HTTPException(
            400, "Idempotency-Key is required (8-128 letters, digits, dots, colons, hyphens or underscores)",
        )

    db = ingest._connect()
    try:
        _initialize(db)
        ingest._janitor(db)
        connection, raw_body = await machine_auth.authorize(request, db, "provision")
        try:
            payload = ProvisionRequest.model_validate_json(raw_body)
        except ValidationError as exc:
            raise HTTPException(422, json.loads(exc.json())) from exc
        request_hash = hashlib.sha256(raw_body).hexdigest()
        project, replay = _reserve(db, connection["id"], key, request_hash, payload)
    finally:
        db.close()

    if replay is not None:
        return JSONResponse(replay, headers={"Idempotent-Replayed": "true"})

    return await asyncio.to_thread(
        _execute_reserved, connection["id"], key, project, payload,
    )


@router.get("/api/provision/status")
async def provision_status(request: Request):
    db = ingest._connect()
    try:
        _initialize(db)
        ingest._janitor(db)
        connection, _ = await machine_auth.authorize(request, db, "provision")
        _discard_missing_sites(db)
        rows = db.execute(
            "SELECT project,status,expires_at,created_at FROM provision_sites "
            "WHERE connection_id=? ORDER BY created_at DESC",
            (connection["id"],),
        ).fetchall()
        db.commit()
    finally:
        db.close()

    quota = _connection_quota()
    try:
        catalog = cockpit.blueprint_catalog()
    except HTTPException:
        catalog = {"blueprints": []}
    return {
        "api_version": API_VERSION,
        "spawnwp_version": ingest.spawnwp_version(),
        "connection": {
            "id": connection["id"],
            "label": connection["label"],
            "scope": connection["scope"],
        },
        "defaults": {
            "expires_seconds": 3600,
            "role": "administrator",
            "group": "API",
            "access_profile": "standard",
            "credentials_mode": "return",
        },
        "limits": {
            "min_expires_seconds": 300,
            "max_expires_seconds": 365 * 86400,
            "concurrent_sites": quota,
            "provision_timeout_seconds": PROVISION_TIMEOUT_SECONDS,
        },
        "active_sites": len(rows),
        "sites": [_site_summary(row) for row in rows],
        "blueprints": [
            {
                "id": item.get("id", ""),
                "name": item.get("name", item.get("id", "")),
                "version": item.get("version", ""),
                "source": item.get("source", ""),
            }
            for item in catalog.get("blueprints", [])
        ],
    }


@router.post("/api/provision/sites/{project}/magic-link")
async def provision_magic_link(project: str, request: Request):
    """Mint access only for an active site owned by the signed connection."""
    db = ingest._connect()
    try:
        _initialize(db)
        ingest._janitor(db)
        connection, _ = await machine_auth.authorize(request, db, "provision")
        _discard_missing_sites(db)
        row = db.execute(
            "SELECT project,status,expires_at,username,credentials_mode FROM provision_sites "
            "WHERE project=? AND connection_id=?",
            (project, connection["id"]),
        ).fetchone()
        db.commit()
    finally:
        db.close()
    if not row or row["status"] != "active":
        raise HTTPException(404, "Active provisioned site not found")
    if row["expires_at"] <= int(time.time()):
        raise HTTPException(410, "Provisioned site has expired")
    if row["credentials_mode"] != "managed":
        raise HTTPException(409, "Provisioned site does not use managed credentials")
    if not row["username"]:
        raise HTTPException(409, "Provisioned site has no managed user")
    proj = cockpit.resolve_project(project)
    return cockpit.mint_magic_login(proj, row["username"])


@router.delete("/api/provision/connection")
async def revoke_provision_connection(request: Request):
    db = ingest._connect()
    try:
        _initialize(db)
        ingest._janitor(db)
        connection, _ = await machine_auth.authorize(request, db, "provision")
        _discard_missing_sites(db)
        active_sites = db.execute(
            "SELECT COUNT(*) FROM provision_sites WHERE connection_id=?",
            (connection["id"],),
        ).fetchone()[0]
        now = int(time.time())
        db.execute(
            "UPDATE connections SET status='revoked',public_key='',private_key='',"
            "pair_token_hash='',updated_at=? WHERE id=?",
            (now, connection["id"]),
        )
        # Completed responses contain one-time WordPress credentials and are no
        # longer useful once the caller has deliberately revoked its key.
        db.execute(
            "DELETE FROM provision_requests WHERE connection_id=?",
            (connection["id"],),
        )
        db.execute("DELETE FROM nonces WHERE connection_id=?", (connection["id"],))
        db.commit()
    finally:
        db.close()
    return {"status": "revoked", "active_sites": active_sites}
