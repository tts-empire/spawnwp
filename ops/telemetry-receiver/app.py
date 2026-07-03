#!/usr/bin/env python3
"""Minimal self-hosted receiver for opt-in SpawnWP telemetry."""
import hashlib
import hmac
import json
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

DB_PATH = Path(os.environ.get("SPAWNWP_TELEMETRY_DB", "/var/lib/spawnwp-telemetry-receiver/telemetry.sqlite3"))
KEY_PATH = Path(os.environ.get("SPAWNWP_TELEMETRY_HASH_KEY", "/etc/spawnwp-telemetry-receiver.key"))
RETENTION_SECONDS = 90 * 24 * 60 * 60
MAX_CLOCK_SKEW_SECONDS = 48 * 60 * 60

CONTACT_SPOOL = Path(os.environ.get("SPAWNWP_CONTACT_SPOOL", "/var/lib/spawnwp-contact"))
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    installation_id: UUID
    timestamp: datetime

    @field_validator("timestamp")
    @classmethod
    def valid_timestamp(cls, value):
        if value.tzinfo is None:
            raise ValueError("timestamp must include a timezone")
        if abs(value.timestamp() - time.time()) > MAX_CLOCK_SKEW_SECONDS:
            raise ValueError("timestamp is outside the accepted window")
        return value


class DisableEvent(StrictModel):
    event: Literal["disable"]


# Aggregate machine counters accepted from notice-v3 clients. Must match the
# sender's whitelist (installer/telemetry.py METRIC_KEYS).
METRIC_KEYS = frozenset({
    "creates_total", "creates_failed", "healthcheck_timeouts",
    "create_warm_count", "create_warm_seconds_sum", "create_warm_seconds_max",
    "create_cold_count", "create_cold_seconds_sum", "create_cold_seconds_max",
    "blueprint_clean", "blueprint_demo", "blueprint_development", "blueprint_custom",
    "sites_temporary_created", "sites_expired_auto", "php_settings_customized",
    "destroys_total", "php_switches", "image_refreshes", "image_deletes",
})


class HardwareInfo(BaseModel):
    """Rounded machine specs — coarse by design, nothing identifying."""
    model_config = ConfigDict(extra="forbid")
    cpu_count: int = Field(ge=0, le=1024)
    ram_gb: int = Field(ge=0, le=4096)
    disk_total_gb: int = Field(ge=0, le=100000)
    disk_free_gb: int = Field(ge=0, le=100000)
    docker_images_gb: int = Field(ge=0, le=100000)
    build_cache_gb: int = Field(ge=0, le=100000)
    php_versions: int = Field(ge=0, le=64)


class UsageEvent(StrictModel):
    event: Literal["installation", "heartbeat"]
    spawnwp_version: str = Field(pattern=r"^\d+\.\d+\.\d+$", max_length=32)
    os_family: str = Field(min_length=1, max_length=32)
    os_version: str = Field(min_length=1, max_length=160)
    architecture: str = Field(min_length=1, max_length=32)
    features: dict[str, bool]
    counters: dict[Literal["environments_current"], int]
    metrics: dict[str, int] | None = None      # notice v3 only
    hardware: HardwareInfo | None = None       # notice v3 only

    @field_validator("features")
    @classmethod
    def valid_features(cls, value):
        if len(value) > 16 or any(not key or len(key) > 48 for key in value):
            raise ValueError("invalid feature flags")
        return value

    @field_validator("counters")
    @classmethod
    def valid_counters(cls, value):
        count = value.get("environments_current", -1)
        if not 0 <= count <= 10000:
            raise ValueError("invalid environment count")
        return value

    @field_validator("metrics")
    @classmethod
    def valid_metrics(cls, value):
        if value is None:
            return value
        if len(value) > 32:
            raise ValueError("too many metric keys")
        for key, count in value.items():
            if key not in METRIC_KEYS:
                raise ValueError(f"unknown metric key: {key}")
            if not 0 <= count <= 10**9:
                raise ValueError(f"metric out of range: {key}")
        return value


def connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""CREATE TABLE IF NOT EXISTS installations (
        installation_hash TEXT PRIMARY KEY,
        first_seen INTEGER NOT NULL,
        last_seen INTEGER NOT NULL,
        spawnwp_version TEXT NOT NULL,
        os_family TEXT NOT NULL,
        os_version TEXT NOT NULL,
        architecture TEXT NOT NULL,
        features_json TEXT NOT NULL,
        environments_current INTEGER NOT NULL,
        heartbeat_count INTEGER NOT NULL,
        metrics_json TEXT,
        hardware_json TEXT
    )""")
    # Databases created before the metrics/hardware columns (pre-0.3.16): add them.
    for column in ("metrics_json", "hardware_json"):
        try:
            db.execute(f"ALTER TABLE installations ADD COLUMN {column} TEXT")
        except sqlite3.OperationalError:
            pass
    return db


def installation_hash(identifier):
    key = KEY_PATH.read_bytes().strip()
    if len(key) < 32:
        raise RuntimeError("telemetry hash key is missing or too short")
    return hmac.new(key, str(identifier).encode(), hashlib.sha256).hexdigest()


def purge(db, now=None):
    cutoff = int(now or time.time()) - RETENTION_SECONDS
    return db.execute("DELETE FROM installations WHERE last_seen < ?", (cutoff,)).rowcount


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/health")
def health():
    with connection() as db:
        db.execute("SELECT 1")
    return {"status": "ok"}


@app.post("/api/v1/telemetry", status_code=202)
def receive(payload: UsageEvent | DisableEvent, response: Response):
    try:
        identifier = installation_hash(payload.installation_id)
    except OSError as exc:
        raise HTTPException(503, "receiver is not configured") from exc
    now = int(time.time())
    with connection() as db:
        purge(db, now)
        if payload.event == "disable":
            db.execute("DELETE FROM installations WHERE installation_hash = ?", (identifier,))
        else:
            metrics_json = (json.dumps(payload.metrics, sort_keys=True, separators=(",", ":"))
                            if payload.metrics is not None else None)
            hardware_json = (json.dumps(payload.hardware.model_dump(), sort_keys=True,
                                        separators=(",", ":"))
                             if payload.hardware is not None else None)
            db.execute("""INSERT INTO installations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(installation_hash) DO UPDATE SET
                last_seen=excluded.last_seen, spawnwp_version=excluded.spawnwp_version,
                os_family=excluded.os_family, os_version=excluded.os_version,
                architecture=excluded.architecture, features_json=excluded.features_json,
                environments_current=excluded.environments_current,
                heartbeat_count=installations.heartbeat_count + 1,
                metrics_json=excluded.metrics_json, hardware_json=excluded.hardware_json""",
                (identifier, now, now, payload.spawnwp_version, payload.os_family,
                 payload.os_version, payload.architecture,
                 json.dumps(payload.features, sort_keys=True, separators=(",", ":")),
                 payload.counters["environments_current"], metrics_json, hardware_json))
    response.headers["Cache-Control"] = "no-store"
    return {"accepted": True}


class ContactMessage(BaseModel):
    """A message composed by the website contact concierge."""
    model_config = ConfigDict(extra="forbid")
    intent: Literal["support", "bug", "business", "security"]
    email: str = Field(min_length=3, max_length=254)
    message: str = Field(min_length=1, max_length=4000)
    consent: Literal[True]
    website: str = Field(default="", max_length=200)  # honeypot; must stay empty

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        value = value.strip()
        if not EMAIL_RE.match(value):
            raise ValueError("invalid email address")
        return value


@app.post("/api/v1/contact", status_code=202)
def contact(payload: ContactMessage, response: Response):
    response.headers["Cache-Control"] = "no-store"
    # A filled honeypot means a bot; accept silently without spooling anything.
    if payload.website.strip():
        return {"accepted": True}
    record = {
        "id": str(uuid4()),
        "received_at": datetime.now(timezone.utc).isoformat(),
        "intent": payload.intent,
        "email": payload.email,
        "message": payload.message,
        "consent": payload.consent,
    }
    CONTACT_SPOOL.mkdir(parents=True, exist_ok=True)
    # Write to a temp name and rename so the mailer never reads a partial file.
    final = CONTACT_SPOOL / f"{record['id']}.json"
    staging = final.with_suffix(".json.tmp")
    staging.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    os.chmod(staging, 0o640)
    staging.rename(final)
    return {"accepted": True}


if __name__ == "__main__":
    import sys
    if sys.argv[1:] != ["cleanup"]:
        raise SystemExit("usage: app.py cleanup")
    with connection() as database:
        print(f"Purged {purge(database)} expired telemetry records")
