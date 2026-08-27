#!/usr/bin/env python3
"""Provision and revoke least-privilege core API credentials for local modules."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

import ingest
import machine_auth

MODULE_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
ALLOWED_SCOPES = {"ingest", "provision"}
MODULES_ROOT = Path(os.environ.get("SPAWNWP_MODULES_ROOT", "/opt/spawnwp/modules"))
CREDENTIALS_ROOT = Path(os.environ.get(
    "SPAWNWP_MODULE_CREDENTIALS_ROOT", "/var/lib/spawnwp/modules",
))


class ModuleAPIError(RuntimeError):
    pass


def _manifest(module_id: str) -> dict:
    if not MODULE_ID_RE.fullmatch(module_id):
        raise ModuleAPIError("Invalid module id")
    path = MODULES_ROOT / module_id / "current" / "module.json"
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ModuleAPIError(f"Installed module manifest is unavailable: {module_id}") from exc
    if value.get("id") != module_id:
        raise ModuleAPIError("Installed module manifest id does not match")
    return value


def _server_url() -> str:
    config = Path(os.environ.get("SPAWNWP_CONFIG", "/etc/spawnwp/config.env"))
    try:
        for line in config.read_text().splitlines():
            if line.startswith("COCKPIT_DOMAIN="):
                hostname = line.split("=", 1)[1].strip()
                if hostname:
                    return f"https://{hostname}"
    except OSError as exc:
        raise ModuleAPIError(f"Unable to read SpawnWP configuration: {exc}") from exc
    raise ModuleAPIError("COCKPIT_DOMAIN is not configured")


def credential_path(module_id: str) -> Path:
    return CREDENTIALS_ROOT / module_id / "api-credential.json"


def _read_credential(path: Path, module_id: str, scope: str) -> dict | None:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    required = {"version", "module_id", "scope", "server_url", "public_hostname",
                "connection_id", "private_key", "public_key"}
    if not isinstance(value, dict) or not required.issubset(value):
        return None
    if value["version"] != 1 or value["module_id"] != module_id or value["scope"] != scope:
        return None
    return value


def _write_credential(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".api-credential-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def ensure(module_id: str, scope: str) -> dict:
    manifest = _manifest(module_id)
    if scope not in ALLOWED_SCOPES or manifest.get("core_api_scope") != scope:
        raise ModuleAPIError("Requested core API scope is not declared by the signed module")
    path = credential_path(module_id)
    existing = _read_credential(path, module_id, scope)
    db = ingest._connect()
    try:
        row = db.execute(
            "SELECT * FROM connections WHERE connection_kind='local_module' AND module_id=?",
            (module_id,),
        ).fetchone()
        if (existing and row and row["status"] == "active"
                and row["id"] == existing["connection_id"]
                and row["public_key"] == existing["public_key"]
                and row["scope"] == scope):
            return {"connection_id": row["id"], "scope": scope, "created": False,
                    "credential_path": str(path)}

        keys = machine_auth.generate_keypair()
        connection_id = row["id"] if row else secrets.token_hex(16)
        now = int(time.time())
        label = str(manifest.get("name") or module_id)[:80]
        if row:
            db.execute(
                "UPDATE connections SET label=?,remote_host=?,public_key=?,private_key='',"
                "pair_token_hash='',pair_expires=0,status='active',scope=?,"
                "connection_kind='local_module',module_id=?,updated_at=? WHERE id=?",
                (label, "local module", keys["public"], scope, module_id, now, connection_id),
            )
            db.execute("DELETE FROM nonces WHERE connection_id=?", (connection_id,))
        else:
            db.execute(
                "INSERT INTO connections(id,label,remote_host,public_key,private_key,"
                "pair_token_hash,pair_expires,status,scope,connection_kind,module_id,"
                "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (connection_id, label, "local module", keys["public"], "", "", 0,
                 "active", scope, "local_module", module_id, now, now),
            )
        db.commit()
        server_url = _server_url()
        credential = {
            "version": 1,
            "module_id": module_id,
            "scope": scope,
            "server_url": server_url,
            "public_hostname": urlparse(server_url).hostname,
            "connection_id": connection_id,
            "private_key": keys["private"],
            "public_key": keys["public"],
        }
        _write_credential(path, credential)
        return {"connection_id": connection_id, "scope": scope, "created": True,
                "credential_path": str(path)}
    finally:
        db.close()


def revoke(module_id: str) -> dict:
    _manifest(module_id)
    db = ingest._connect()
    try:
        rows = db.execute(
            "SELECT id FROM connections WHERE connection_kind='local_module' AND module_id=?",
            (module_id,),
        ).fetchall()
        now = int(time.time())
        for row in rows:
            connection_id = row["id"]
            db.execute(
                "UPDATE connections SET status='revoked',public_key='',private_key='',"
                "pair_token_hash='',updated_at=? WHERE id=?",
                (now, connection_id),
            )
            db.execute("DELETE FROM nonces WHERE connection_id=?", (connection_id,))
            if db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='provision_requests'",
            ).fetchone():
                db.execute("DELETE FROM provision_requests WHERE connection_id=?", (connection_id,))
        db.commit()
    finally:
        db.close()
    credential_path(module_id).unlink(missing_ok=True)
    return {"module_id": module_id, "revoked": len(rows)}


def main() -> int:
    if os.geteuid() != 0:
        raise SystemExit("Local module credentials must be managed as root")
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    issue = sub.add_parser("ensure")
    issue.add_argument("module_id")
    issue.add_argument("scope")
    remove = sub.add_parser("revoke")
    remove.add_argument("module_id")
    args = parser.parse_args()
    try:
        result = ensure(args.module_id, args.scope) if args.command == "ensure" else revoke(args.module_id)
    except ModuleAPIError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
