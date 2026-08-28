"""Fetch and verify the signed SpawnWP free-module catalog."""

from __future__ import annotations

import base64
import json
import os
import re
import ssl
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

MODULE_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
DEFAULT_URL = "https://spawnwp.com/modules/catalog.json"
PUBLIC_KEY = Path(os.environ.get("SPAWNWP_MODULE_PUBLIC_KEY", "/usr/local/lib/spawnwp/module-public.pem"))
MAX_BYTES = 2 * 1024 * 1024
CACHE_TTL = 300
_cache: tuple[float, dict] | None = None


class CatalogError(RuntimeError):
    pass


def _version(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or not SEMVER_RE.fullmatch(value):
        raise CatalogError(f"Invalid semantic version: {value!r}")
    return tuple(int(part) for part in value.split("."))


def canonical_json(payload: dict) -> bytes:
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _signature_url(url: str) -> str:
    configured = os.environ.get("SPAWNWP_MODULE_CATALOG_SIGNATURE_URL")
    if configured:
        return configured
    return url[:-5] + ".sig.b64" if url.endswith(".json") else url + ".sig.b64"


def _fetch(url: str) -> bytes:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise CatalogError("Catalog URLs must use HTTPS")
    request = urllib.request.Request(url, headers={"User-Agent": "SpawnWP-Cockpit/1"})
    try:
        with urllib.request.urlopen(request, timeout=10, context=ssl.create_default_context()) as response:
            declared = response.headers.get("Content-Length")
            if declared and int(declared) > MAX_BYTES:
                raise CatalogError("Catalog exceeds the size limit")
            chunks, total = [], 0
            while True:
                chunk = response.read(min(65536, MAX_BYTES - total + 1))
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_BYTES:
                    raise CatalogError("Catalog exceeds the size limit")
                chunks.append(chunk)
            return b"".join(chunks)
    except CatalogError:
        raise
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise CatalogError(f"Unable to fetch module catalog: {exc}") from exc


def _verify(payload: bytes, signature_b64: bytes) -> None:
    if not PUBLIC_KEY.is_file():
        raise CatalogError(f"Module signing key not found: {PUBLIC_KEY}")
    try:
        signature = base64.b64decode(signature_b64.strip(), validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise CatalogError("Catalog signature is not valid base64") from exc
    with tempfile.TemporaryDirectory(prefix="spawnwp-catalog-") as directory:
        root = Path(directory)
        data_path, sig_path = root / "catalog.json", root / "catalog.sig"
        data_path.write_bytes(payload)
        sig_path.write_bytes(signature)
        result = subprocess.run([
            "openssl", "pkeyutl", "-verify", "-pubin", "-inkey", str(PUBLIC_KEY),
            "-rawin", "-in", str(data_path), "-sigfile", str(sig_path),
        ], capture_output=True, text=True)
    if result.returncode != 0:
        raise CatalogError("Catalog signature verification failed")


def validate(payload: dict, current_core: str) -> dict:
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        raise CatalogError("Catalog schema is invalid")
    catalog_version = payload.get("catalog_version")
    if not isinstance(catalog_version, int) or catalog_version < 1:
        raise CatalogError("Catalog version is invalid")
    publisher = payload.get("publisher")
    modules = payload.get("modules")
    if not isinstance(publisher, str) or not publisher.strip() or not isinstance(modules, list):
        raise CatalogError("Catalog metadata is invalid")
    current = _version(current_core)
    seen = set()
    clean = []
    for item in modules:
        if not isinstance(item, dict):
            raise CatalogError("Catalog module entry is invalid")
        module_id = item.get("id")
        if not isinstance(module_id, str) or not MODULE_ID_RE.fullmatch(module_id) or module_id in seen:
            raise CatalogError("Catalog contains an invalid or duplicate module id")
        seen.add(module_id)
        version = item.get("version")
        minimum = item.get("min_core_version", "0.0.0")
        maximum = item.get("max_core_version", "9999.0.0")
        _version(version); min_v = _version(minimum); max_v = _version(maximum)
        if min_v > max_v:
            raise CatalogError(f"Catalog core range is invalid for {module_id}")
        if item.get("license") != "free":
            raise CatalogError(f"Catalog module {module_id} is not free")
        archive = item.get("archive_url")
        parsed = urllib.parse.urlparse(archive or "")
        if parsed.scheme != "https" or not parsed.netloc or not str(archive).endswith(".tar.gz"):
            raise CatalogError(f"Catalog archive URL is invalid for {module_id}")
        for key in ("name", "description", "publisher"):
            if not isinstance(item.get(key), str) or not item[key].strip():
                raise CatalogError(f"Catalog field {key} is invalid for {module_id}")
        if current < min_v or current > max_v:
            continue
        clean.append(item)
    return {"schema": 1, "catalog_version": catalog_version, "publisher": publisher, "modules": clean}


def load(current_core: str, *, force: bool = False) -> dict:
    global _cache
    url = os.environ.get("SPAWNWP_MODULE_CATALOG_URL", DEFAULT_URL)
    if not force and _cache and time.monotonic() - _cache[0] < CACHE_TTL and _cache[1].get("core") == current_core and _cache[1].get("url") == url:
        return _cache[1]["catalog"]
    raw = _fetch(url)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CatalogError("Catalog JSON is invalid") from exc
    try:
        signature = _fetch(_signature_url(url))
        _verify(canonical_json(payload), signature)
    except CatalogError:
        raise
    catalog = validate(payload, current_core)
    _cache = (time.monotonic(), {"core": current_core, "url": url, "catalog": catalog})
    return catalog
