"""Blueprint ingest: pairing and signed chunked uploads from the SpawnWP Deploy plugin.

The /api/ingest/* endpoints are reachable without a cockpit session (see the
middleware allowlist in app.py); every request except the initial pairing must
carry the X-SpawnWP-* signature headers verified by machine_auth. State lives
in a root-only sqlite database; payload archives are staged under
<payloads>/.staging/<job> and the blueprint manifest — the only file
blueprint.py discover() reads — is written last, so a failed ingest can never
leave a half-installed blueprint behind.
"""

import asyncio
import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import time
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

import machine_auth

router = APIRouter()

INGEST_FORMAT = 1
MAX_ARCHIVE_BYTES = 2 * 1024**3
MAX_CHUNK_BYTES = 8 * 1024**2
MAX_CHUNKS = 16384
PAIRING_TTL = 900
NONCE_TTL = 600
STAGING_TTL = 86400
JOB_ROW_TTL = 7 * 86400
EXPANSION_LIMIT = 5
BLUEPRINT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
ALLOWED_TOP_LEVEL = {"database.jsonl", "content"}


def _db_path() -> Path:
    return Path(os.environ.get("SPAWNWP_INGEST_DB", "/var/lib/spawnwp/ingest.db"))


def _payload_root() -> Path:
    return Path(os.environ.get("SPAWNWP_BLUEPRINT_PAYLOADS", "/var/lib/spawnwp/blueprints"))


def _custom_dir() -> Path:
    return Path(os.environ.get("SPAWNWP_CUSTOM_BLUEPRINTS", "/etc/spawnwp/blueprints.d"))


def _blueprint_tool() -> Path:
    return Path(os.environ.get("SPAWNWP_BLUEPRINT_TOOL", "/srv/wp-dev/scripts/blueprint.py"))


def _staging_root() -> Path:
    return _payload_root() / ".staging"


def _spawnwp_version() -> str:
    try:
        return Path(os.environ.get("SPAWNWP_VERSION_FILE", "/var/lib/spawnwp/VERSION")).read_text().strip()
    except OSError:
        return "unknown"


def _server_url(request: Request) -> str:
    config = Path(os.environ.get("SPAWNWP_CONFIG", "/etc/spawnwp/config.env"))
    try:
        for line in config.read_text().splitlines():
            if line.startswith("COCKPIT_DOMAIN="):
                return f"https://{line.split('=', 1)[1].strip()}"
    except OSError:
        pass
    return f"https://{request.url.hostname or 'localhost'}"


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not path.exists()
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS connections(
            id TEXT PRIMARY KEY, label TEXT NOT NULL DEFAULT '',
            remote_host TEXT NOT NULL DEFAULT '', public_key TEXT NOT NULL DEFAULT '',
            private_key TEXT NOT NULL DEFAULT '', pair_token_hash TEXT NOT NULL DEFAULT '',
            pair_expires INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL,
            created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs(
            id TEXT PRIMARY KEY, connection_id TEXT NOT NULL, state TEXT NOT NULL,
            blueprint_json TEXT NOT NULL, replace_existing INTEGER NOT NULL DEFAULT 0,
            archive_bytes INTEGER NOT NULL, archive_sha256 TEXT NOT NULL,
            chunk_size INTEGER NOT NULL, chunk_count INTEGER NOT NULL,
            received INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS nonces(
            connection_id TEXT NOT NULL, nonce_hash TEXT NOT NULL,
            created_at INTEGER NOT NULL, UNIQUE(connection_id, nonce_hash));
    """)
    if fresh:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    return db


def _janitor(db: sqlite3.Connection) -> None:
    now = int(time.time())
    db.execute("DELETE FROM nonces WHERE created_at < ?", (now - NONCE_TTL,))
    db.execute("UPDATE connections SET status='expired', updated_at=? "
               "WHERE status='pending' AND pair_expires < ?", (now, now))
    db.execute("DELETE FROM jobs WHERE state IN ('complete','failed') AND updated_at < ?",
               (now - JOB_ROW_TTL,))
    db.commit()
    staging = _staging_root()
    if staging.is_dir():
        for entry in staging.iterdir():
            try:
                if entry.stat().st_mtime < now - STAGING_TTL:
                    shutil.rmtree(entry, ignore_errors=True)
            except OSError:
                continue


async def _authorize(request: Request, db: sqlite3.Connection) -> tuple[sqlite3.Row, bytes]:
    connection_id = request.headers.get("x-spawnwp-connection", "")
    nonce = request.headers.get("x-spawnwp-nonce", "")
    signature = request.headers.get("x-spawnwp-signature", "")
    try:
        timestamp = int(request.headers.get("x-spawnwp-timestamp", ""))
    except ValueError:
        timestamp = 0
    if not connection_id or not timestamp or len(nonce) < machine_auth.MIN_NONCE_LENGTH or not signature:
        raise HTTPException(401, "Signed connection headers are required")
    row = db.execute("SELECT * FROM connections WHERE id=? AND status='active'",
                     (connection_id,)).fetchone()
    body = await request.body()
    if not row or not machine_auth.verify(row["public_key"], signature, request.method,
                                          request.url.path, timestamp, nonce, body):
        raise HTTPException(401, "Request signature is invalid")
    inserted = db.execute("INSERT OR IGNORE INTO nonces(connection_id, nonce_hash, created_at) "
                          "VALUES (?,?,?)",
                          (connection_id, hashlib.sha256(nonce.encode()).hexdigest(),
                           int(time.time()))).rowcount
    db.commit()
    if inserted != 1:
        raise HTTPException(409, "Request nonce has already been used")
    return row, body


def _catalog_ids() -> dict[str, str]:
    result = subprocess.run(["python3", str(_blueprint_tool()), "list"],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise HTTPException(500, "Unable to load the blueprint catalog")
    catalog = json.loads(result.stdout)
    ids = {item["id"]: item["source"] for item in catalog["blueprints"]}
    for error in catalog["errors"]:
        ids.setdefault(Path(error["file"]).stem, "invalid")
    return ids


def _validate_proposal(manifest: dict, blueprint_id: str) -> None:
    result = subprocess.run(
        ["python3", str(_blueprint_tool()), "validate-stdin",
         "--filename", f"{blueprint_id}.json", "--skip-payload"],
        input=json.dumps(manifest), capture_output=True, text=True)
    if result.returncode != 0:
        detail = result.stderr.strip().removeprefix("ERROR: ").strip()
        raise HTTPException(422, detail or "Blueprint manifest is invalid")


def _metric_incr(key: str, n: int = 1) -> None:
    metrics = Path(os.environ.get("SPAWNWP_METRICS_FILE", "/var/lib/spawnwp/metrics.json"))
    import fcntl
    try:
        metrics.parent.mkdir(parents=True, exist_ok=True)
        with open(f"{metrics}.lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                data = json.loads(metrics.read_text())
                if not isinstance(data, dict):
                    data = {}
            except (OSError, ValueError):
                data = {}
            data[key] = int(data.get(key, 0)) + n
            tmp = Path(f"{metrics}.tmp")
            tmp.write_text(json.dumps(data, sort_keys=True))
            tmp.replace(metrics)
    except OSError:
        pass


# ── Session-authenticated endpoints (cockpit UI) ───────────────────────────────

@router.post("/api/blueprint-pairings")
def create_pairing(request: Request):
    db = _connect()
    try:
        _janitor(db)
        keys = machine_auth.generate_keypair()
        pairing_id = secrets.token_hex(16)
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        expires = now + PAIRING_TTL
        db.execute("INSERT INTO connections(id, private_key, public_key, pair_token_hash, "
                   "pair_expires, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                   (pairing_id, keys["private"], keys["public"],
                    hashlib.sha256(token.encode()).hexdigest(), expires, "pending", now, now))
        db.commit()
        bundle = {
            "version": 1,
            "server_url": _server_url(request),
            "pairing_id": pairing_id,
            "token": token,
            "server_public_key": keys["public"],
            "expires": expires,
        }
        encoded = base64.urlsafe_b64encode(json.dumps(bundle, separators=(",", ":")).encode()).decode().rstrip("=")
        return {"pairing_id": pairing_id, "bundle": f"spawnbp1:{encoded}", "expires": expires}
    finally:
        db.close()


@router.get("/api/blueprint-pairings")
def list_pairings():
    db = _connect()
    try:
        _janitor(db)
        rows = db.execute("SELECT id, label, remote_host, status, pair_expires, created_at "
                          "FROM connections WHERE status IN ('pending','active') "
                          "ORDER BY created_at DESC").fetchall()
        return {"connections": [dict(row) for row in rows]}
    finally:
        db.close()


@router.delete("/api/blueprint-connections/{connection_id}")
def revoke_connection(connection_id: str):
    db = _connect()
    try:
        updated = db.execute("UPDATE connections SET status='revoked', public_key='', "
                             "private_key='', pair_token_hash='', updated_at=? WHERE id=?",
                             (int(time.time()), connection_id)).rowcount
        db.commit()
        if not updated:
            raise HTTPException(404, "Unknown connection")
        return {"status": "revoked"}
    finally:
        db.close()


@router.delete("/api/blueprints/{blueprint_id}")
def delete_blueprint(blueprint_id: str):
    if not BLUEPRINT_ID_RE.fullmatch(blueprint_id):
        raise HTTPException(400, "Invalid blueprint id")
    manifest_path = _custom_dir() / f"{blueprint_id}.json"
    if not manifest_path.is_file():
        raise HTTPException(404, "Unknown blueprint")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        manifest = {}
    if manifest.get("schema_version") != 2:
        raise HTTPException(400, "Only content blueprints can be deleted from the cockpit")
    manifest_path.unlink()
    shutil.rmtree(_payload_root() / blueprint_id, ignore_errors=True)
    return {"status": "deleted"}


# ── Machine-authenticated endpoints (plugin) ───────────────────────────────────

@router.post("/api/ingest/pair")
async def pair(request: Request):
    db = _connect()
    try:
        _janitor(db)
        try:
            data = json.loads(await request.body())
        except json.JSONDecodeError:
            raise HTTPException(400, "Invalid pairing payload")
        for field in ("pairing_id", "token", "source_public_key", "source_host", "proof"):
            if not isinstance(data.get(field), str) or not data[field]:
                raise HTTPException(400, "Invalid pairing payload")
        row = db.execute("SELECT * FROM connections WHERE id=? AND status='pending'",
                         (data["pairing_id"],)).fetchone()
        if not row or row["pair_expires"] < int(time.time()):
            raise HTTPException(403, "Pairing key is invalid or expired")
        token_hash = hashlib.sha256(data["token"].encode()).hexdigest()
        if not secrets.compare_digest(row["pair_token_hash"], token_hash):
            raise HTTPException(403, "Pairing key is invalid or expired")
        proof_data = f"pair|{row['id']}|{data['source_public_key']}|{data['source_host']}"
        if not machine_auth.verify_detached(data["source_public_key"], data["proof"],
                                            proof_data.encode()):
            raise HTTPException(403, "Pairing proof is invalid")
        db.execute("UPDATE connections SET status='active', public_key=?, remote_host=?, "
                   "label=?, pair_token_hash='', pair_expires=0, updated_at=? WHERE id=?",
                   (data["source_public_key"], data["source_host"][:100],
                    str(data.get("label", ""))[:80], int(time.time()), row["id"]))
        db.commit()
        return {
            "connection_id": row["id"],
            "server_public_key": row["public_key"],
            "ingest_format": INGEST_FORMAT,
            "spawnwp_version": _spawnwp_version(),
        }
    finally:
        db.close()


@router.get("/api/ingest/preflight")
async def preflight(request: Request):
    db = _connect()
    try:
        _janitor(db)
        await _authorize(request, db)
        root = _payload_root()
        root.mkdir(parents=True, exist_ok=True)
        return {
            "spawnwp_version": _spawnwp_version(),
            "ingest_format": INGEST_FORMAT,
            "max_archive_bytes": MAX_ARCHIVE_BYTES,
            "max_chunk_bytes": MAX_CHUNK_BYTES,
            "free_bytes": shutil.disk_usage(root).free,
            "existing_blueprint_ids": _catalog_ids(),
        }
    finally:
        db.close()


@router.post("/api/ingest/jobs")
async def create_job(request: Request):
    db = _connect()
    try:
        _janitor(db)
        connection, body = await _authorize(request, db)
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            raise HTTPException(400, "Invalid job payload")
        blueprint = data.get("blueprint")
        archive = data.get("archive")
        replace = bool(data.get("replace", False))
        if not isinstance(blueprint, dict) or not isinstance(archive, dict):
            raise HTTPException(400, "Job payload must include blueprint and archive")
        blueprint_id = blueprint.get("id", "")
        if not isinstance(blueprint_id, str) or not BLUEPRINT_ID_RE.fullmatch(blueprint_id):
            raise HTTPException(422, "Invalid blueprint id")
        archive_bytes = archive.get("bytes")
        archive_sha256 = archive.get("sha256", "")
        chunk_size = archive.get("chunk_size")
        chunk_count = archive.get("chunk_count")
        if not isinstance(archive_bytes, int) or not 1 <= archive_bytes <= MAX_ARCHIVE_BYTES:
            raise HTTPException(422, "archive.bytes must be between 1 and 2 GiB")
        if not isinstance(archive_sha256, str) or not HEX64_RE.fullmatch(archive_sha256):
            raise HTTPException(422, "archive.sha256 must be 64 lowercase hex characters")
        if not isinstance(chunk_size, int) or not 1 <= chunk_size <= MAX_CHUNK_BYTES:
            raise HTTPException(422, "archive.chunk_size is out of range")
        expected_chunks = -(-archive_bytes // chunk_size)
        if not isinstance(chunk_count, int) or chunk_count != expected_chunks or chunk_count > MAX_CHUNKS:
            raise HTTPException(422, "archive.chunk_count does not match archive.bytes")
        proposal = dict(blueprint)
        proposal["payload"] = {"file": "payload.zip", "bytes": archive_bytes,
                               "sha256": archive_sha256}
        _validate_proposal(proposal, blueprint_id)
        existing = _catalog_ids()
        if blueprint_id in existing:
            manifest_path = _custom_dir() / f"{blueprint_id}.json"
            replaceable = False
            if manifest_path.is_file():
                try:
                    replaceable = json.loads(manifest_path.read_text()).get("schema_version") == 2
                except (OSError, json.JSONDecodeError):
                    replaceable = False
            if not (replace and replaceable):
                raise HTTPException(409, f"Blueprint id '{blueprint_id}' already exists"
                                    + ("" if replaceable else " and cannot be replaced"))
        active = db.execute("SELECT id FROM jobs WHERE connection_id=? AND state IN "
                            "('uploading','finalizing')", (connection["id"],)).fetchone()
        if active:
            raise HTTPException(409, "Another upload is already in progress on this connection")
        root = _payload_root()
        root.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(root).free < 2 * archive_bytes:
            raise HTTPException(507, "Not enough free disk space on the SpawnWP server")
        job_id = secrets.token_hex(16)
        now = int(time.time())
        db.execute("INSERT INTO jobs(id, connection_id, state, blueprint_json, replace_existing, "
                   "archive_bytes, archive_sha256, chunk_size, chunk_count, created_at, updated_at) "
                   "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (job_id, connection["id"], "uploading", json.dumps(blueprint),
                    int(replace), archive_bytes, archive_sha256, chunk_size, chunk_count, now, now))
        db.commit()
        (_staging_root() / job_id / "chunks").mkdir(parents=True, exist_ok=True)
        return {"job_id": job_id, "chunk_size": chunk_size, "chunk_count": chunk_count}
    finally:
        db.close()


def _load_job(db: sqlite3.Connection, connection_id: str, job_id: str) -> sqlite3.Row:
    job = db.execute("SELECT * FROM jobs WHERE id=? AND connection_id=?",
                     (job_id, connection_id)).fetchone()
    if not job:
        raise HTTPException(404, "Unknown ingest job")
    return job


@router.put("/api/ingest/jobs/{job_id}/chunks/{index}")
async def upload_chunk(job_id: str, index: int, request: Request):
    db = _connect()
    try:
        connection, body = await _authorize(request, db)
        job = _load_job(db, connection["id"], job_id)
        if job["state"] != "uploading":
            raise HTTPException(409, f"Job is not accepting chunks (state: {job['state']})")
        if not 0 <= index < job["chunk_count"]:
            raise HTTPException(422, "Chunk index is out of range")
        if not body or len(body) > job["chunk_size"]:
            raise HTTPException(422, "Chunk size is out of range")
        expected = request.headers.get("x-spawnwp-chunk-sha256", "")
        if hashlib.sha256(body).hexdigest() != expected:
            raise HTTPException(422, "Chunk checksum mismatch")
        chunks_dir = _staging_root() / job_id / "chunks"
        chunks_dir.mkdir(parents=True, exist_ok=True)
        (chunks_dir / str(index)).write_bytes(body)
        received = len(list(chunks_dir.iterdir()))
        db.execute("UPDATE jobs SET received=?, updated_at=? WHERE id=?",
                   (received, int(time.time()), job_id))
        db.commit()
        return {"received": received, "of": job["chunk_count"]}
    finally:
        db.close()


@router.post("/api/ingest/jobs/{job_id}/finalize")
async def finalize_job(job_id: str, request: Request):
    db = _connect()
    try:
        connection, _ = await _authorize(request, db)
        job = _load_job(db, connection["id"], job_id)
        if job["state"] != "uploading":
            raise HTTPException(409, f"Job cannot be finalized (state: {job['state']})")
        if job["received"] != job["chunk_count"]:
            raise HTTPException(409, "Not all chunks have been uploaded")
        db.execute("UPDATE jobs SET state='finalizing', updated_at=? WHERE id=?",
                   (int(time.time()), job_id))
        db.commit()
    finally:
        db.close()
    asyncio.get_running_loop().run_in_executor(None, _finalize, job_id)
    return JSONResponse({"state": "finalizing"}, status_code=202)


@router.get("/api/ingest/jobs/{job_id}")
async def job_status(job_id: str, request: Request):
    db = _connect()
    try:
        connection, _ = await _authorize(request, db)
        job = _load_job(db, connection["id"], job_id)
        return {"state": job["state"], "received": job["received"],
                "of": job["chunk_count"], "error": job["error"]}
    finally:
        db.close()


@router.delete("/api/ingest/connection")
async def revoke_own_connection(request: Request):
    db = _connect()
    try:
        connection, _ = await _authorize(request, db)
        db.execute("UPDATE connections SET status='revoked', public_key='', private_key='', "
                   "updated_at=? WHERE id=?", (int(time.time()), connection["id"]))
        db.commit()
        return {"status": "revoked"}
    finally:
        db.close()


# ── Finalize (runs in a worker thread) ─────────────────────────────────────────

def _harden_archive(path: Path, archive_bytes: int, expect_database: bool) -> None:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        total = 0
        for info in archive.infolist():
            name = info.filename
            if name.startswith("/") or "\\" in name or "\x00" in name:
                raise ValueError(f"Archive entry has an unsafe path: {name}")
            parts = name.split("/")
            if ".." in parts or parts[0] not in ALLOWED_TOP_LEVEL:
                raise ValueError(f"Archive entry has an unsafe path: {name}")
            if (info.external_attr >> 16) & 0xF000 == 0xA000:
                raise ValueError(f"Archive entry is a symlink: {name}")
            total += info.file_size
        if total > EXPANSION_LIMIT * max(archive_bytes, 1024**2):
            raise ValueError("Archive expands beyond the allowed limit")
        has_database = "database.jsonl" in names
        if expect_database != has_database:
            raise ValueError("Archive contents do not match the capture flags")


def _finalize(job_id: str) -> None:
    db = _connect()
    staging = _staging_root() / job_id
    try:
        job = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            return
        blueprint = json.loads(job["blueprint_json"])
        blueprint_id = blueprint["id"]
        assembled = staging / "payload.zip"
        digest = hashlib.sha256()
        size = 0
        with open(assembled, "wb") as target:
            for index in range(job["chunk_count"]):
                chunk = (staging / "chunks" / str(index)).read_bytes()
                digest.update(chunk)
                size += len(chunk)
                target.write(chunk)
        if size != job["archive_bytes"] or digest.hexdigest() != job["archive_sha256"]:
            raise ValueError("Assembled archive does not match the announced checksum")
        expect_database = bool(blueprint.get("capture", {}).get("database"))
        _harden_archive(assembled, job["archive_bytes"], expect_database)
        payload_name = f"payload-{job_id[:8]}.zip"
        manifest = dict(blueprint)
        manifest["payload"] = {"file": payload_name, "bytes": job["archive_bytes"],
                               "sha256": job["archive_sha256"]}
        result = subprocess.run(
            ["python3", str(_blueprint_tool()), "validate-stdin",
             "--filename", f"{blueprint_id}.json", "--skip-payload"],
            input=json.dumps(manifest), capture_output=True, text=True)
        if result.returncode != 0:
            raise ValueError(result.stderr.strip().removeprefix("ERROR: ").strip()
                             or "Final manifest failed validation")
        dest_dir = _payload_root() / blueprint_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(assembled), dest_dir / payload_name)
        manifest_path = _custom_dir() / f"{blueprint_id}.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = manifest_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, manifest_path)
        for stale in dest_dir.glob("payload*.zip"):
            if stale.name != payload_name:
                stale.unlink(missing_ok=True)
        db.execute("UPDATE jobs SET state='complete', updated_at=? WHERE id=?",
                   (int(time.time()), job_id))
        db.commit()
        _metric_incr("blueprint_captures")
    except Exception as exc:  # a failed ingest must never leave a manifest behind
        db.execute("UPDATE jobs SET state='failed', error=?, updated_at=? WHERE id=?",
                   (str(exc)[:500], int(time.time()), job_id))
        db.commit()
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        db.close()
