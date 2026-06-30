"""Local single-administrator authentication for the SpawnWP cockpit."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from webauthn import (
    base64url_to_bytes,
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

DB_PATH = Path(os.environ.get("SPAWNWP_AUTH_DB", "/var/lib/spawnwp/auth.db"))
KEY_PATH = Path(os.environ.get("SPAWNWP_AUTH_KEY", "/etc/spawnwp/auth.key"))
CONFIG_PATH = Path(os.environ.get("SPAWNWP_CONFIG", "/etc/spawnwp/config.env"))
COOKIE = "spawnwp_session"
CSRF_COOKIE = "spawnwp_csrf"
IDLE_SECONDS = 30 * 60
ABSOLUTE_SECONDS = 12 * 60 * 60
CHALLENGE_SECONDS = 5 * 60
BOOTSTRAP_SECONDS = 24 * 60 * 60
PASSWORD_HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)
router = APIRouter(prefix="/api/auth")


def _config() -> dict[str, str]:
    result = {}
    if CONFIG_PATH.is_file():
        for line in CONFIG_PATH.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                result[key.strip()] = value.strip()
    return result


def _key() -> bytes:
    try:
        key = KEY_PATH.read_bytes().strip()
        Fernet(key)
        return key
    except (OSError, ValueError) as exc:
        raise RuntimeError("SpawnWP authentication key is missing or invalid") from exc


def _digest(value: str | bytes) -> str:
    raw = value.encode() if isinstance(value, str) else value
    return hmac.new(base64.urlsafe_b64decode(_key()), raw, hashlib.sha256).hexdigest()


def _encrypt(value: str) -> str:
    return Fernet(_key()).encrypt(value.encode()).decode()


def _decrypt(value: str) -> str:
    try:
        return Fernet(_key()).decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError("Unable to decrypt authentication data") from exc


def _qr_data_uri(value: str) -> str:
    import qrcode
    import qrcode.image.svg
    output = __import__("io").BytesIO()
    qrcode.make(value, image_factory=qrcode.image.svg.SvgPathImage,
                box_size=5, border=2).save(output)
    return "data:image/svg+xml;base64," + base64.b64encode(output.getvalue()).decode()


@contextmanager
def db(immediate: bool = False):
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        if immediate:
            connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize() -> None:
    _key()
    with db() as connection:
        connection.executescript("""
        CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
        INSERT INTO schema_version(version) SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM schema_version);
        CREATE TABLE IF NOT EXISTS bootstrap (
          id INTEGER PRIMARY KEY CHECK(id=1), code_hash TEXT NOT NULL, expires_at INTEGER NOT NULL,
          used_at INTEGER, attempts INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS admins (
          id INTEGER PRIMARY KEY, user_id BLOB NOT NULL UNIQUE, username TEXT NOT NULL UNIQUE,
          password_hash TEXT NOT NULL, totp_secret TEXT NOT NULL, last_totp_step INTEGER,
          created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS passkeys (
          id INTEGER PRIMARY KEY, admin_id INTEGER NOT NULL REFERENCES admins(id) ON DELETE CASCADE,
          credential_id BLOB NOT NULL UNIQUE, public_key BLOB NOT NULL, sign_count INTEGER NOT NULL,
          transports TEXT NOT NULL, name TEXT NOT NULL, created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS recovery_codes (
          id INTEGER PRIMARY KEY, admin_id INTEGER NOT NULL REFERENCES admins(id) ON DELETE CASCADE,
          code_hash TEXT NOT NULL UNIQUE, used_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS challenges (
          id_hash TEXT PRIMARY KEY, kind TEXT NOT NULL, challenge BLOB NOT NULL,
          payload TEXT NOT NULL, expires_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
          id_hash TEXT PRIMARY KEY, admin_id INTEGER NOT NULL REFERENCES admins(id) ON DELETE CASCADE,
          csrf_hash TEXT NOT NULL, created_at INTEGER NOT NULL, last_seen INTEGER NOT NULL,
          recent_auth INTEGER NOT NULL, absolute_expires INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS rate_limits (
          bucket TEXT PRIMARY KEY, window_start INTEGER NOT NULL, attempts INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS audit (
          id INTEGER PRIMARY KEY, event TEXT NOT NULL, remote_hash TEXT,
          created_at INTEGER NOT NULL, detail TEXT NOT NULL DEFAULT ''
        );
        """)
    os.chmod(DB_PATH, 0o600)


def create_bootstrap(code: str | None = None, *, reset_admin: bool = False) -> str:
    initialize()
    code = code or secrets.token_urlsafe(32)
    now = int(time.time())
    with db(immediate=True) as connection:
        connection.execute("DELETE FROM sessions")
        if reset_admin:
            connection.execute("DELETE FROM admins")
        connection.execute(
            "INSERT OR REPLACE INTO bootstrap(id,code_hash,expires_at,used_at,attempts) VALUES(1,?,?,NULL,0)",
            (_digest(code), now + BOOTSTRAP_SECONDS),
        )
        _audit(connection, "authentication_reset" if reset_admin else "bootstrap_created", None)
    return code


def is_enrolled() -> bool:
    initialize()
    with db() as connection:
        return connection.execute("SELECT 1 FROM admins LIMIT 1").fetchone() is not None


def _audit(connection, event: str, request: Request | None, detail: str = "") -> None:
    remote = request.client.host if request and request.client else ""
    connection.execute(
        "INSERT INTO audit(event,remote_hash,created_at,detail) VALUES(?,?,?,?)",
        (event, _digest(remote) if remote else None, int(time.time()), detail[:120]),
    )


def _rate_limit(connection, request: Request, action: str, limit: int = 8) -> None:
    remote = request.client.host if request.client else "unknown"
    bucket = _digest(f"{action}:{remote}")
    now = int(time.time())
    row = connection.execute("SELECT * FROM rate_limits WHERE bucket=?", (bucket,)).fetchone()
    if not row or now - row["window_start"] >= 300:
        connection.execute("INSERT OR REPLACE INTO rate_limits VALUES(?,?,1)", (bucket, now))
    elif row["attempts"] >= limit:
        raise HTTPException(429, "Too many attempts. Try again later.")
    else:
        connection.execute("UPDATE rate_limits SET attempts=attempts+1 WHERE bucket=?", (bucket,))


def _challenge(kind: str, challenge: bytes, payload: dict) -> str:
    identifier = secrets.token_urlsafe(32)
    with db() as connection:
        connection.execute(
            "INSERT INTO challenges VALUES(?,?,?,?,?)",
            (_digest(identifier), kind, challenge, json.dumps(payload), int(time.time()) + CHALLENGE_SECONDS),
        )
    return identifier


def _consume_challenge(connection, identifier: str, kind: str):
    row = connection.execute(
        "SELECT * FROM challenges WHERE id_hash=? AND kind=?", (_digest(identifier), kind)
    ).fetchone()
    connection.execute("DELETE FROM challenges WHERE id_hash=?", (_digest(identifier),))
    if not row or row["expires_at"] < int(time.time()):
        raise HTTPException(400, "Authentication ceremony expired or invalid")
    return row


def _set_session(response: Response, admin_id: int) -> None:
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    now = int(time.time())
    with db() as connection:
        connection.execute(
            "INSERT INTO sessions VALUES(?,?,?,?,?,?,?)",
            (_digest(token), admin_id, _digest(csrf), now, now, now, now + ABSOLUTE_SECONDS),
        )
    response.set_cookie(COOKIE, token, secure=True, httponly=True, samesite="strict", max_age=ABSOLUTE_SECONDS)
    response.set_cookie(CSRF_COOKIE, csrf, secure=True, httponly=False, samesite="strict", max_age=ABSOLUTE_SECONDS)


def session(request: Request, *, touch: bool = True):
    token = request.cookies.get(COOKIE, "")
    if not token:
        return None
    now = int(time.time())
    with db(immediate=touch) as connection:
        row = connection.execute(
            "SELECT sessions.*,admins.username FROM sessions JOIN admins ON admins.id=sessions.admin_id WHERE id_hash=?",
            (_digest(token),),
        ).fetchone()
        if not row or row["absolute_expires"] < now or now - row["last_seen"] > IDLE_SECONDS:
            connection.execute("DELETE FROM sessions WHERE id_hash=?", (_digest(token),))
            return None
        if touch:
            connection.execute("UPDATE sessions SET last_seen=? WHERE id_hash=?", (now, _digest(token)))
        return dict(row)


def valid_csrf(request: Request, active_session: dict) -> bool:
    supplied = request.headers.get("x-csrf-token", "")
    return bool(supplied and hmac.compare_digest(_digest(supplied), active_session["csrf_hash"]))


class SetupStart(BaseModel):
    bootstrap_code: str = Field(min_length=20, max_length=256)
    username: str = Field(pattern=r"^[A-Za-z0-9_.-]{3,32}$")
    password: str = Field(min_length=14, max_length=512)


class CeremonyFinish(BaseModel):
    ceremony: str
    credential: dict
    totp: str = Field(pattern=r"^\d{6}$")
    passkey_name: str = Field(default="Primary passkey", min_length=1, max_length=64)


class PasskeyFinish(BaseModel):
    ceremony: str
    credential: dict


class FallbackLogin(BaseModel):
    username: str
    password: str
    second_factor: str = Field(min_length=6, max_length=64)


@router.get("/state")
def auth_state(request: Request):
    active = session(request)
    return {"enrolled": is_enrolled(), "authenticated": bool(active),
            "username": active["username"] if active else None}


@router.post("/setup/start")
def setup_start(body: SetupStart, request: Request):
    if is_enrolled():
        raise HTTPException(409, "Administrator already enrolled")
    with db(immediate=True) as connection:
        _rate_limit(connection, request, "setup")
        row = connection.execute("SELECT * FROM bootstrap WHERE id=1").fetchone()
        now = int(time.time())
        if not row or row["used_at"] or row["expires_at"] < now or not hmac.compare_digest(row["code_hash"], _digest(body.bootstrap_code)):
            _audit(connection, "setup_rejected", request)
            raise HTTPException(400, "Activation code is invalid, expired or already used")
    config = _config()
    rp_id = config.get("COCKPIT_DOMAIN", request.url.hostname or "localhost")
    user_id = secrets.token_bytes(32)
    options = generate_registration_options(
        rp_id=rp_id, rp_name="SpawnWP", user_name=body.username, user_id=user_id,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
    )
    totp_secret = pyotp.random_base32(length=32)
    payload = {"username": body.username, "password_hash": PASSWORD_HASHER.hash(body.password),
               "totp": _encrypt(totp_secret), "user_id": base64.b64encode(user_id).decode(),
               "bootstrap_hash": _digest(body.bootstrap_code)}
    ceremony = _challenge("setup", options.challenge, payload)
    uri = pyotp.TOTP(totp_secret, digest=hashlib.sha256).provisioning_uri(
        name=body.username, issuer_name="SpawnWP")
    return {"ceremony": ceremony, "publicKey": json.loads(options_to_json(options)),
            "totp_qr": _qr_data_uri(uri), "totp_secret": totp_secret}


@router.post("/setup/finish")
def setup_finish(body: CeremonyFinish, request: Request, response: Response):
    config = _config()
    rp_id = config.get("COCKPIT_DOMAIN", request.url.hostname or "localhost")
    origin = f"https://{rp_id}"
    with db(immediate=True) as connection:
        challenge = _consume_challenge(connection, body.ceremony, "setup")
        payload = json.loads(challenge["payload"])
        bootstrap = connection.execute("SELECT * FROM bootstrap WHERE id=1").fetchone()
        if not bootstrap or bootstrap["used_at"] or not hmac.compare_digest(bootstrap["code_hash"], payload["bootstrap_hash"]):
            raise HTTPException(400, "Activation code is invalid, expired or already used")
        secret = _decrypt(payload["totp"])
        totp = pyotp.TOTP(secret, digest=hashlib.sha256)
        if not totp.verify(body.totp, valid_window=2):
            raise HTTPException(400, (
                "Authenticator code rejected. Check the server clock is accurate "
                "(sudo timedatectl set-ntp true) and that you scanned the QR code with an "
                "app that supports SHA-256 (Aegis, 2FAS, 1Password, Bitwarden) instead of "
                "typing the secret by hand."
            ))
        try:
            verified = verify_registration_response(
                credential=body.credential, expected_challenge=challenge["challenge"],
                expected_rp_id=rp_id, expected_origin=origin, require_user_verification=True,
            )
        except Exception as exc:
            raise HTTPException(400, "Passkey registration failed") from exc
        now = int(time.time())
        cursor = connection.execute(
            "INSERT INTO admins(user_id,username,password_hash,totp_secret,last_totp_step,created_at) VALUES(?,?,?,?,?,?)",
            (base64.b64decode(payload["user_id"]), payload["username"], payload["password_hash"],
             payload["totp"], totp.timecode(__import__("datetime").datetime.now()), now),
        )
        admin_id = cursor.lastrowid
        connection.execute(
            "INSERT INTO passkeys(admin_id,credential_id,public_key,sign_count,transports,name,created_at) VALUES(?,?,?,?,?,?,?)",
            (admin_id, verified.credential_id, verified.credential_public_key, verified.sign_count,
             json.dumps(body.credential.get("response", {}).get("transports", [])), body.passkey_name, now),
        )
        codes = [f"{secrets.token_hex(4)}-{secrets.token_hex(4)}" for _ in range(10)]
        connection.executemany("INSERT INTO recovery_codes(admin_id,code_hash) VALUES(?,?)",
                               [(admin_id, _digest(code)) for code in codes])
        connection.execute("UPDATE bootstrap SET used_at=? WHERE id=1", (now,))
        _audit(connection, "administrator_enrolled", request)
    _set_session(response, admin_id)
    return {"ok": True, "recovery_codes": codes}


@router.post("/passkey/start")
def passkey_start(request: Request):
    with db() as connection:
        rows = connection.execute("SELECT credential_id FROM passkeys").fetchall()
    if not rows:
        raise HTTPException(400, "No passkeys enrolled")
    rp_id = _config().get("COCKPIT_DOMAIN", request.url.hostname or "localhost")
    options = generate_authentication_options(
        rp_id=rp_id, allow_credentials=[PublicKeyCredentialDescriptor(id=row[0]) for row in rows],
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    return {"ceremony": _challenge("login", options.challenge, {}),
            "publicKey": json.loads(options_to_json(options))}


@router.post("/passkey/finish")
def passkey_finish(body: PasskeyFinish, request: Request, response: Response):
    credential_id = base64url_to_bytes(body.credential.get("id", ""))
    with db(immediate=True) as connection:
        _rate_limit(connection, request, "passkey")
        challenge = _consume_challenge(connection, body.ceremony, "login")
        key = connection.execute("SELECT * FROM passkeys WHERE credential_id=?", (credential_id,)).fetchone()
        if not key:
            raise HTTPException(400, "Authentication failed")
        rp_id = _config().get("COCKPIT_DOMAIN", request.url.hostname or "localhost")
        try:
            verified = verify_authentication_response(
                credential=body.credential, expected_challenge=challenge["challenge"],
                expected_rp_id=rp_id, expected_origin=f"https://{rp_id}",
                credential_public_key=key["public_key"], credential_current_sign_count=key["sign_count"],
                require_user_verification=True,
            )
        except Exception as exc:
            _audit(connection, "login_rejected", request, "passkey")
            raise HTTPException(400, "Authentication failed") from exc
        connection.execute("UPDATE passkeys SET sign_count=? WHERE id=?", (verified.new_sign_count, key["id"]))
        _audit(connection, "login_success", request, "passkey")
        admin_id = key["admin_id"]
    _set_session(response, admin_id)
    return {"ok": True}


@router.post("/reauth/start")
def reauth_start(request: Request):
    active = session(request, touch=False)
    if not active:
        raise HTTPException(401, "Authentication required")
    with db() as connection:
        rows = connection.execute(
            "SELECT credential_id FROM passkeys WHERE admin_id=?", (active["admin_id"],)
        ).fetchall()
    if not rows:
        raise HTTPException(400, "No passkey is registered for this administrator")
    rp_id = _config().get("COCKPIT_DOMAIN", request.url.hostname or "localhost")
    options = generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=[PublicKeyCredentialDescriptor(id=row[0]) for row in rows],
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    token = request.cookies.get(COOKIE, "")
    payload = {"admin_id": active["admin_id"], "session_hash": _digest(token)}
    return {
        "ceremony": _challenge("reauth", options.challenge, payload),
        "publicKey": json.loads(options_to_json(options)),
    }


@router.post("/reauth/finish")
def reauth_finish(body: PasskeyFinish, request: Request):
    active = session(request, touch=False)
    if not active:
        raise HTTPException(401, "Authentication required")
    token = request.cookies.get(COOKIE, "")
    session_hash = _digest(token)
    credential_id = base64url_to_bytes(body.credential.get("id", ""))
    with db(immediate=True) as connection:
        _rate_limit(connection, request, "reauth")
        challenge = _consume_challenge(connection, body.ceremony, "reauth")
    payload = json.loads(challenge["payload"])
    if payload.get("admin_id") != active["admin_id"] or not hmac.compare_digest(
        payload.get("session_hash", ""), session_hash
    ):
        with db(immediate=True) as connection:
            _audit(connection, "reauth_rejected", request, "session mismatch")
        raise HTTPException(400, "Authentication ceremony does not belong to this session")
    with db(immediate=True) as connection:
        key = connection.execute(
            "SELECT * FROM passkeys WHERE credential_id=? AND admin_id=?",
            (credential_id, active["admin_id"]),
        ).fetchone()
        if not key:
            raise HTTPException(400, "Authentication failed")
        rp_id = _config().get("COCKPIT_DOMAIN", request.url.hostname or "localhost")
        try:
            verified = verify_authentication_response(
                credential=body.credential,
                expected_challenge=challenge["challenge"],
                expected_rp_id=rp_id,
                expected_origin=f"https://{rp_id}",
                credential_public_key=key["public_key"],
                credential_current_sign_count=key["sign_count"],
                require_user_verification=True,
            )
        except Exception as exc:
            _audit(connection, "reauth_rejected", request, "passkey")
            raise HTTPException(400, "Authentication failed") from exc
        now = int(time.time())
        connection.execute(
            "UPDATE passkeys SET sign_count=? WHERE id=?", (verified.new_sign_count, key["id"])
        )
        connection.execute(
            "UPDATE sessions SET recent_auth=? WHERE id_hash=?", (now, session_hash)
        )
        _audit(connection, "reauth_success", request, "passkey")
    return {"ok": True, "recent_auth": now}


@router.post("/fallback")
def fallback_login(body: FallbackLogin, request: Request, response: Response):
    with db(immediate=True) as connection:
        _rate_limit(connection, request, "fallback")
        admin = connection.execute("SELECT * FROM admins WHERE username=?", (body.username,)).fetchone()
        valid_password = False
        if admin:
            try:
                valid_password = PASSWORD_HASHER.verify(admin["password_hash"], body.password)
            except VerifyMismatchError:
                pass
        if not admin or not valid_password:
            _audit(connection, "login_rejected", request, "fallback")
            raise HTTPException(400, "Authentication failed")
        accepted = False
        if body.second_factor.isdigit() and len(body.second_factor) == 6:
            totp = pyotp.TOTP(_decrypt(admin["totp_secret"]), digest=hashlib.sha256)
            import datetime
            now = datetime.datetime.now()
            for offset in (-2, -1, 0, 1, 2):
                step = totp.timecode(now) + offset
                if step > (admin["last_totp_step"] or -1) and hmac.compare_digest(totp.at(now, offset), body.second_factor):
                    connection.execute("UPDATE admins SET last_totp_step=? WHERE id=?", (step, admin["id"]))
                    accepted = True
                    break
        else:
            code = connection.execute(
                "SELECT id FROM recovery_codes WHERE admin_id=? AND code_hash=? AND used_at IS NULL",
                (admin["id"], _digest(body.second_factor)),
            ).fetchone()
            if code:
                connection.execute("UPDATE recovery_codes SET used_at=? WHERE id=?", (int(time.time()), code["id"]))
                accepted = True
        if not accepted:
            _audit(connection, "login_rejected", request, "second_factor")
            raise HTTPException(400, "Authentication failed")
        _audit(connection, "login_success", request, "fallback")
        admin_id = admin["id"]
    _set_session(response, admin_id)
    return {"ok": True}


@router.post("/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE, "")
    if token:
        with db() as connection:
            connection.execute("DELETE FROM sessions WHERE id_hash=?", (_digest(token),))
            _audit(connection, "logout", request)
    response.delete_cookie(COOKIE)
    response.delete_cookie(CSRF_COOKIE)
    return {"ok": True}


@router.get("/check")
def auth_check(request: Request):
    if not session(request):
        raise HTTPException(401, "Authentication required")
    return Response(status_code=204)


LOGIN_HTML = r"""<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>SpawnWP login</title><style>
:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;padding:48px 0;background:#000;color:#f8f8f8;font:14px system-ui;min-height:100vh;display:flex;justify-content:center;align-items:flex-start}.box{width:min(440px,calc(100% - 32px));border-top:2px solid #f6b269;padding:28px 0}h1{font-size:24px;margin:0 0 8px}h2{font-size:18px;margin:22px 0 6px}p{color:#a1a1aa;line-height:1.5;margin:6px 0}.step{color:#f6b269;font-size:11px;font-weight:700;text-transform:uppercase}.help{font-size:12px}.notice{border-left:2px solid #3a3a40;padding-left:12px;margin:14px 0}label{display:block;margin:14px 0 6px;font-size:11px;text-transform:uppercase;color:#a1a1aa}input,button{width:100%;padding:11px;background:#151518;color:#fff;border:1px solid #3a3a40;border-radius:4px}button{margin-top:14px;background:#f6b269;color:#0d0d10;font-weight:700;cursor:pointer}button:disabled{opacity:.55;cursor:wait}.alt{background:#202024;color:#fff}.small{width:auto;margin:8px 0 0;padding:8px 10px}.err{color:#ff7373;min-height:20px}.ok{color:#7bd88f}.hide{display:none}code{word-break:break-all;color:#f6b269}.secret,.codes{white-space:pre-wrap;background:#0d0d10;padding:12px;border:1px solid #2b2b30;border-radius:4px}.codes{line-height:1.7}#qr{display:block;width:220px;height:220px;margin:16px auto;background:#fff;padding:12px;border-radius:4px}@media(max-width:480px){body{padding:24px 0}}</style></head><body><main class=box><h1>Spawn<span style='color:#f6b269'>WP</span></h1><p id=intro>Secure cockpit access</p>
<section id=login><button onclick=passkey()>Sign in with passkey</button><form onsubmit='fallback(event)'><label>Username</label><input id=user autocomplete=username required><label>Password</label><input id=pass type=password autocomplete=current-password required><label>Authenticator code or recovery code</label><input id=second autocomplete=one-time-code required><button class=alt>Sign in with password</button></form></section>
<section id=setup class=hide>
<form id=identity onsubmit='startSetup(event)'><p class=step>Step 1 of 3 · Administrator</p><h2>Create cockpit access</h2><p>Use the one-time activation code from the installation report. Then choose the password used when signing in without a passkey; a TOTP or recovery code will also be required.</p><label>One-time activation code</label><input id=boot type=password autocomplete=off required><label>Administrator username</label><input id=newuser autocomplete=username pattern='[A-Za-z0-9_.-]{3,32}' required><label>Password (14+ characters)</label><input id=newpass type=password autocomplete=new-password minlength=14 required><p class=help>Used only when signing in without a passkey. A TOTP or recovery code is also required.</p><button>Continue</button></form>
<div id=totp class=hide><p class=step>Step 2 of 3 · Two-factor protection</p><h2>Add an authenticator app</h2><p>Scan this QR code with any TOTP authenticator. Examples include 2FAS, Aegis (Android), Google Authenticator, Microsoft Authenticator, 1Password and Bitwarden.</p><img id=qr alt='QR code for the SpawnWP authenticator account'><div class=notice><p class=help>Cannot scan the QR code? Add an account manually with this secret:</p><div class=secret><code id=secret></code></div><button type=button class='alt small' onclick="copyValue('secret',this)">Copy secret</button></div><label>6-digit code shown by the authenticator app</label><input id=otp inputmode=numeric autocomplete=one-time-code pattern='[0-9]{6}' minlength=6 maxlength=6 required><h2>Create a passkey</h2><p>Your browser will ask to save a passkey using this device, its PIN or biometrics, a security key, or your password manager. This is the normal sign-in method.</p><button id=finish type=button onclick=finishSetup()>Verify code and create passkey</button></div>
<div id=recovery class=hide><p class=step>Step 3 of 3 · Recovery</p><h2>Save your recovery codes</h2><p>Each code works once, together with your password. They will not be shown again. Store them outside this server, preferably in a password manager.</p><div class=codes id=codes></div><button type=button class=alt onclick="copyValue('codes',this)">Copy all recovery codes</button><button onclick="location.href='/manage'">I have stored the codes</button></div>
</section><p class=err id=err aria-live=polite></p></main><script>
let ceremony,creation;const $=id=>document.getElementById(id);function b64(v){let s=btoa(String.fromCharCode(...new Uint8Array(v)));return s.replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'')}function bytes(v){v=v.replace(/-/g,'+').replace(/_/g,'/');return Uint8Array.from(atob(v),c=>c.charCodeAt(0))}function prep(o){o.challenge=bytes(o.challenge);if(o.user)o.user.id=bytes(o.user.id);if(o.excludeCredentials)o.excludeCredentials.forEach(x=>x.id=bytes(x.id));if(o.allowCredentials)o.allowCredentials.forEach(x=>x.id=bytes(x.id));return o}function cred(c){return{id:c.id,rawId:b64(c.rawId),type:c.type,authenticatorAttachment:c.authenticatorAttachment,clientExtensionResults:c.getClientExtensionResults(),response:{clientDataJSON:b64(c.response.clientDataJSON),attestationObject:c.response.attestationObject?b64(c.response.attestationObject):undefined,authenticatorData:c.response.authenticatorData?b64(c.response.authenticatorData):undefined,signature:c.response.signature?b64(c.response.signature):undefined,userHandle:c.response.userHandle?b64(c.response.userHandle):null,transports:c.response.getTransports?c.response.getTransports():[]}}}async function api(url,data){let r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data||{})});let j=await r.json();if(!r.ok)throw Error(j.detail||'Authentication failed');return j}function fail(e){$('err').textContent=e.message||e}function clearError(){$('err').textContent=''}async function copyValue(id,button){try{await navigator.clipboard.writeText($(id).textContent);let old=button.textContent;button.textContent='Copied';setTimeout(()=>button.textContent=old,1500)}catch(e){fail(Error('Copy failed. Select the text and copy it manually.'))}}async function state(){let s=await fetch('/api/auth/state').then(r=>r.json());if(s.authenticated)location.href='/manage';else if(!s.enrolled){$('login').classList.add('hide');$('setup').classList.remove('hide');$('intro').textContent='One-time administrator activation'}}async function startSetup(e){e.preventDefault();clearError();try{let j=await api('/api/auth/setup/start',{bootstrap_code:$('boot').value,username:$('newuser').value,password:$('newpass').value});ceremony=j.ceremony;creation=j.publicKey;$('secret').textContent=j.totp_secret;$('qr').src=j.totp_qr;e.target.classList.add('hide');$('totp').classList.remove('hide');$('otp').focus()}catch(e){fail(e)}}async function finishSetup(){clearError();if(!$('otp').checkValidity()){$('otp').reportValidity();return}if(!window.PublicKeyCredential){fail(Error('This browser cannot create passkeys. Use a current browser with WebAuthn support.'));return}let button=$('finish');button.disabled=true;button.textContent='Waiting for passkey…';try{let c=await navigator.credentials.create({publicKey:prep(creation)});if(!c)throw Error('Passkey creation was cancelled.');let j=await api('/api/auth/setup/finish',{ceremony,credential:cred(c),totp:$('otp').value,passkey_name:'Primary passkey'});$('totp').classList.add('hide');$('recovery').classList.remove('hide');$('codes').textContent=j.recovery_codes.join('\n')}catch(e){fail(e)}finally{button.disabled=false;button.textContent='Verify code and create passkey'}}async function passkey(){clearError();try{let j=await api('/api/auth/passkey/start',{});let c=await navigator.credentials.get({publicKey:prep(j.publicKey)});await api('/api/auth/passkey/finish',{ceremony:j.ceremony,credential:cred(c)});location.href='/manage'}catch(e){fail(e)}}async function fallback(e){e.preventDefault();clearError();try{await api('/api/auth/fallback',{username:$('user').value,password:$('pass').value,second_factor:$('second').value});location.href='/manage'}catch(e){fail(e)}}state().catch(fail);
</script></body></html>"""


def login_page() -> HTMLResponse:
    return HTMLResponse(LOGIN_HTML, headers={"Cache-Control": "no-store"})
