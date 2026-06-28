#!/usr/bin/env python3
"""Minimal self-hosted receiver for opt-in SpawnWP telemetry."""
import hashlib
import hmac
import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

DB_PATH = Path(os.environ.get("SPAWNWP_TELEMETRY_DB", "/var/lib/spawnwp-telemetry-receiver/telemetry.sqlite3"))
KEY_PATH = Path(os.environ.get("SPAWNWP_TELEMETRY_HASH_KEY", "/etc/spawnwp-telemetry-receiver.key"))
RETENTION_SECONDS = 90 * 24 * 60 * 60
MAX_CLOCK_SKEW_SECONDS = 48 * 60 * 60


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


class UsageEvent(StrictModel):
    event: Literal["installation", "heartbeat"]
    spawnwp_version: str = Field(pattern=r"^\d+\.\d+\.\d+$", max_length=32)
    os_family: str = Field(min_length=1, max_length=32)
    os_version: str = Field(min_length=1, max_length=160)
    architecture: str = Field(min_length=1, max_length=32)
    features: dict[str, bool]
    counters: dict[Literal["environments_current"], int]

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
        heartbeat_count INTEGER NOT NULL
    )""")
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
            db.execute("""INSERT INTO installations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(installation_hash) DO UPDATE SET
                last_seen=excluded.last_seen, spawnwp_version=excluded.spawnwp_version,
                os_family=excluded.os_family, os_version=excluded.os_version,
                architecture=excluded.architecture, features_json=excluded.features_json,
                environments_current=excluded.environments_current,
                heartbeat_count=installations.heartbeat_count + 1""",
                (identifier, now, now, payload.spawnwp_version, payload.os_family,
                 payload.os_version, payload.architecture,
                 json.dumps(payload.features, sort_keys=True, separators=(",", ":")),
                 payload.counters["environments_current"]))
    response.headers["Cache-Control"] = "no-store"
    return {"accepted": True}


if __name__ == "__main__":
    import sys
    if sys.argv[1:] != ["cleanup"]:
        raise SystemExit("usage: app.py cleanup")
    with connection() as database:
        print(f"Purged {purge(database)} expired telemetry records")
