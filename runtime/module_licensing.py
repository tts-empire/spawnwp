"""Offline-verifiable premium entitlements. No network calls on the runtime path.

Release signatures and entitlement signatures deliberately use different keys.
The CLI is also used by the stdlib-only updater through the cockpit interpreter.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
PRODUCT_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")


class LicenseError(RuntimeError):
    pass


def canonical(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def root() -> Path:
    return Path(os.environ.get("SPAWNWP_LICENSE_STATE", "/var/lib/spawnwp/licenses"))


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".license-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def identity(*, create: bool = False) -> dict:
    path = root() / "identity.json"
    if create:
        # Serialise first activation across cockpit and updater processes.
        import fcntl
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (path.parent / ".identity.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if not path.exists():
                key = Ed25519PrivateKey.generate()
                atomic_json(path, {
                    "installation_id": secrets.token_hex(16),
                    "public_key": base64.b64encode(key.public_key().public_bytes_raw()).decode(),
                    "private_key": base64.b64encode(key.private_bytes_raw()).decode(),
                })
    try:
        value = json.loads(path.read_text())
        key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(value["private_key"], validate=True))
        if base64.b64encode(key.public_key().public_bytes_raw()).decode() != value["public_key"]:
            raise ValueError("identity mismatch")
        if not re.fullmatch(r"[0-9a-f]{32}", value["installation_id"]):
            raise ValueError("invalid installation")
        return value
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise LicenseError("Installation identity is missing or invalid") from exc


def validate_metadata(manifest: dict) -> None:
    if manifest.get("schema") != 2:
        raise LicenseError("Premium modules require manifest schema 2")
    if not ID_RE.fullmatch(str(manifest.get("id", ""))):
        raise LicenseError("Invalid module id")
    if not PRODUCT_RE.fullmatch(str(manifest.get("product_id", ""))):
        raise LicenseError("Invalid premium product id")
    stamp = manifest.get("published_at")
    if type(stamp) is not int or stamp <= 0:
        raise LicenseError("A signed release publication timestamp is required")


def _signing_key(envelope: dict):
    """Return the public key selected by a signed entitlement's key id.

    The keyring is shipped in the signed SpawnWP core release.  Keeping more
    than one key permits the licensing service to rotate its signer without
    invalidating certificates issued before the next core update.
    """
    keyring_path = Path(os.environ.get(
        "SPAWNWP_LICENSE_PUBLIC_KEYRING",
        "/usr/local/lib/spawnwp/license-public-keys.json",
    ))
    try:
        keyring = json.loads(keyring_path.read_text())
        kid = envelope["kid"]
        if (keyring.get("schema") != 1 or not isinstance(kid, str)
                or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", kid)):
            raise ValueError("invalid keyring")
        matches = [item for item in keyring["keys"] if isinstance(item, dict) and item.get("kid") == kid]
        if len(matches) != 1 or not isinstance(matches[0].get("public_key_pem"), str):
            raise ValueError("unknown signing key")
        key = serialization.load_pem_public_key(matches[0]["public_key_pem"].encode())
        if not hasattr(key, "verify"):
            raise ValueError("invalid signing key")
        return key
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise LicenseError("Premium entitlement signing key is missing or unknown") from exc


def verify(envelope: dict, module_id: str, product_id: str) -> dict:
    """An entitlement never expires; only its release eligibility window does."""
    ident = identity()
    try:
        claims = envelope["claims"]
        key = _signing_key(envelope)
        key.verify(base64.b64decode(envelope["signature"], validate=True), canonical(claims))
        if (claims["schema"] != 1 or claims["module_id"] != module_id
                or claims["product_id"] != product_id
                or claims["installation_id"] != ident["installation_id"]
                or claims["public_key"] != ident["public_key"]
                or type(claims["updates_until"]) is not int or claims["updates_until"] <= 0
                or type(claims.get("issued_at")) is not int or claims["issued_at"] <= 0):
            raise ValueError("claim mismatch")
        return claims
    except (OSError, ValueError, TypeError, KeyError, InvalidSignature, AttributeError) as exc:
        raise LicenseError("Premium entitlement is missing, invalid or belongs to another installation") from exc


def certificate(module_id: str) -> dict:
    if not ID_RE.fullmatch(module_id):
        raise LicenseError("Invalid module id")
    try:
        return json.loads((root() / (module_id + ".json")).read_text())
    except (OSError, ValueError) as exc:
        raise LicenseError("Activate a license before installing this premium module") from exc


def check(manifest: dict) -> dict:
    if manifest.get("commercial_model", "free") == "free":
        return {"state": "free", "usable": True}
    validate_metadata(manifest)
    claims = verify(certificate(manifest["id"]), manifest["id"], manifest["product_id"])
    if manifest["published_at"] > claims["updates_until"]:
        raise LicenseError("This release was published after the purchased update period")
    return {"state": "active" if claims["updates_until"] >= int(time.time()) else "updates_expired",
            "usable": True, "updates_until": claims["updates_until"],
            "installation_id": claims["installation_id"]}


def status(manifest: dict) -> dict:
    try:
        return check(manifest)
    except LicenseError as exc:
        return {"state": "activation_required", "usable": False, "message": str(exc)}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise LicenseError("License service redirects are not allowed")


def service_request(action: str, payload: dict) -> dict:
    ident = identity(create=True)
    base = os.environ.get("SPAWNWP_LICENSE_SERVICE", "https://licenses.spawnwp.com").rstrip("/")
    parsed = urllib.parse.urlsplit(base)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
        raise LicenseError("License service must be an HTTPS origin")
    path = "/v1/" + action
    body = canonical({**payload, "installation_id": ident["installation_id"], "public_key": ident["public_key"]})
    timestamp, nonce = str(int(time.time())), secrets.token_hex(16)
    message = f"POST\n{path}\n{timestamp}\n{nonce}\n{hashlib.sha256(body).hexdigest()}".encode()
    key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(ident["private_key"]))
    request = urllib.request.Request(base + path, data=body, headers={
        "Content-Type": "application/json", "X-SpawnWP-Timestamp": timestamp,
        "X-SpawnWP-Nonce": nonce, "X-SpawnWP-Signature": base64.b64encode(key.sign(message)).decode(),
    })
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=15) as response:
            raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise LicenseError("License service response exceeds limit")
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError("invalid response")
            return result
    except urllib.error.HTTPError as exc:
        # The service deliberately returns short, operator-safe problem details
        # for rejected activations and transfers. Preserve that detail instead
        # of misreporting every 4xx as an outage; never echo request data.
        try:
            raw = exc.read(64 * 1024)
            payload = json.loads(raw.decode("utf-8"))
            detail = payload.get("detail") if isinstance(payload, dict) else None
        except (OSError, UnicodeDecodeError, ValueError, AttributeError):
            detail = None
        if isinstance(detail, str) and 0 < len(detail) <= 240:
            raise LicenseError(detail) from exc
        raise LicenseError("License request was rejected by the licensing service") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        # Never include response bodies or request details (possibly license keys).
        raise LicenseError("License service unavailable or request rejected; existing entitlements are unchanged") from exc


def activate(module_id: str, product_id: str, license_key: str) -> dict:
    if not ID_RE.fullmatch(module_id) or not PRODUCT_RE.fullmatch(product_id) or not 8 <= len(license_key) <= 256:
        raise LicenseError("Invalid activation parameters")
    result = service_request("activate", {"module_id": module_id, "product_id": product_id, "license_key": license_key})
    claims = verify(result, module_id, product_id)
    atomic_json(root() / (module_id + ".json"), result)
    return {"activated": True, "updates_until": claims["updates_until"]}


def refresh(module_id: str) -> dict:
    previous = certificate(module_id)
    product_id = previous.get("claims", {}).get("product_id", "")
    verify(previous, module_id, product_id)
    result = service_request("refresh", {"module_id": module_id})
    claims = verify(result, module_id, product_id)
    atomic_json(root() / (module_id + ".json"), result)
    return {"refreshed": True, "updates_until": claims["updates_until"]}


def deactivate(module_id: str) -> dict:
    previous = certificate(module_id)
    verify(previous, module_id, previous.get("claims", {}).get("product_id", ""))
    service_request("deactivate", {"module_id": module_id})
    (root() / (module_id + ".json")).unlink()
    return {"deactivated": True}


def resolve(module_id: str) -> dict:
    previous = certificate(module_id)
    verify(previous, module_id, previous.get("claims", {}).get("product_id", ""))
    return service_request("releases/resolve", {"module_id": module_id,
        "core_version": Path(os.environ.get("SPAWNWP_VERSION_FILE", "/var/lib/spawnwp/VERSION")).read_text().strip()})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["check", "resolve", "catalog-source"])
    args = parser.parse_args()
    try:
        value = json.load(sys.stdin)
        if args.action == "check":
            result = check(value)
        elif args.action == "resolve":
            result = resolve(value["id"])
        else:
            from module_catalog import load
            version = Path(os.environ.get("SPAWNWP_VERSION_FILE", "/var/lib/spawnwp/VERSION")).read_text().strip()
            item = next((item for item in load(version)["modules"] if item["id"] == value["id"]), None)
            if item is None:
                raise LicenseError("Module is not available in the signed catalog; supply an explicit source")
            result = {"source": "premium:" + item["id"] if item.get("commercial_model") == "premium" else item["archive_url"]}
        print(json.dumps(result))
        return 0
    except (LicenseError, OSError, ValueError, KeyError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
