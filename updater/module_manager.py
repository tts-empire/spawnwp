#!/usr/bin/env python3
"""Install and manage separately signed official SpawnWP modules."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

MODULE_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
MODULES_ROOT = Path(os.environ.get("SPAWNWP_MODULES_ROOT", "/opt/spawnwp/modules"))
STATE_ROOT = Path(os.environ.get("SPAWNWP_MODULE_STATE_ROOT", "/var/lib/spawnwp/modules"))
PUBLIC_KEY = Path(os.environ.get(
    "SPAWNWP_MODULE_PUBLIC_KEY", "/usr/local/lib/spawnwp/module-public.pem",
))
CORE_VERSION_FILE = Path(os.environ.get("SPAWNWP_VERSION_FILE", "/var/lib/spawnwp/VERSION"))
COCKPIT_PYTHON = Path(os.environ.get("SPAWNWP_COCKPIT_PYTHON", "/srv/wp-cockpit/venv/bin/python"))
MODULE_API_HELPER = Path(os.environ.get("SPAWNWP_MODULE_API_HELPER", "/srv/wp-cockpit/module_api.py"))
MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024
MAX_EXTRACTED_BYTES = 2 * 1024 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 10_000


class ModuleError(RuntimeError):
    pass


def _state_path(module_id: str) -> Path:
    return STATE_ROOT / module_id / "install.json"


def _read_state(module_id: str) -> dict:
    path = _state_path(module_id)
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {"id": module_id, "status": "active"}
    if not isinstance(value, dict):
        return {"id": module_id, "status": "active"}
    value.setdefault("id", module_id)
    value.setdefault("status", "active")
    return value


def _write_state(module_id: str, state: dict) -> None:
    path = _state_path(module_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(".install.json.new")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def _current_release(module_id: str) -> Path:
    if not MODULE_ID_RE.fullmatch(module_id):
        raise ModuleError("Invalid module id")
    release = MODULES_ROOT / module_id / "current"
    if not release.is_dir():
        raise ModuleError(f"Module '{module_id}' is not installed")
    return release.resolve()


def _manifest(release: Path) -> dict:
    try:
        return json.loads((release / "module.json").read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ModuleError("Installed module manifest is invalid") from exc


def _capabilities(release: Path) -> dict[str, bool]:
    return {
        "activate": (release / "activate.py").is_file(),
        "deactivate": (release / "deactivate.py").is_file(),
        "update": True,
        "uninstall": True,
    }


def version_tuple(value: str) -> tuple[int, int, int]:
    match = SEMVER_RE.fullmatch(value)
    if not match:
        raise ModuleError(f"Invalid semantic version: {value}")
    return tuple(int(part) for part in match.groups())


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_extract(archive: Path, destination: Path) -> None:
    with tarfile.open(archive, "r:gz") as package:
        root = destination.resolve()
        members = package.getmembers()
        if len(members) > MAX_ARCHIVE_MEMBERS or sum(item.size for item in members) > MAX_EXTRACTED_BYTES:
            raise ModuleError("Module archive exceeds the extraction limit")
        for member in members:
            target = (destination / member.name).resolve()
            if target != root and root not in target.parents:
                raise ModuleError("Module archive contains an unsafe path")
            if member.issym() or member.islnk() or member.isdev():
                raise ModuleError("Module archive contains an unsupported entry")
        package.extractall(destination, filter="data")


def _download(url: str, target: Path, maximum: int) -> None:
    with urllib.request.urlopen(url, timeout=30) as response, target.open("wb") as out:
        declared = response.headers.get("Content-Length")
        if declared:
            try:
                declared_size = int(declared)
            except ValueError as exc:
                raise ModuleError("Module download has an invalid size") from exc
            if declared_size > maximum:
                raise ModuleError("Module download exceeds the size limit")
        total = 0
        while True:
            chunk = response.read(min(1024 * 1024, maximum - total + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > maximum:
                raise ModuleError("Module download exceeds the size limit")
            out.write(chunk)


def verify_signature(manifest: Path, signature: Path) -> None:
    if not PUBLIC_KEY.is_file():
        raise ModuleError(f"Module signing key not found: {PUBLIC_KEY}")
    result = subprocess.run([
        "openssl", "pkeyutl", "-verify", "-pubin", "-inkey", str(PUBLIC_KEY),
        "-rawin", "-in", str(manifest), "-sigfile", str(signature),
    ], capture_output=True, text=True)
    if result.returncode != 0:
        raise ModuleError("Module manifest signature verification failed")


def load_manifest(path: Path, archive: Path) -> dict:
    try:
        manifest = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ModuleError("Module manifest is invalid") from exc
    module_id = str(manifest.get("id", ""))
    version = str(manifest.get("version", ""))
    if manifest.get("schema") != 1 or not MODULE_ID_RE.fullmatch(module_id):
        raise ModuleError("Module manifest schema or id is invalid")
    version_tuple(version)
    core_api_scope = manifest.get("core_api_scope")
    if core_api_scope is not None and core_api_scope not in {"ingest", "provision"}:
        raise ModuleError("Module manifest requests an unsupported core API scope")
    files = manifest.get("files")
    if not isinstance(files, list) or len(files) > MAX_ARCHIVE_MEMBERS or not all(
        isinstance(item, dict) and isinstance(item.get("path"), str)
        and isinstance(item.get("sha256"), str) for item in files
    ):
        raise ModuleError("Module manifest file list is invalid")
    if sha256(archive) != manifest.get("archive_sha256"):
        raise ModuleError("Module archive checksum mismatch")
    try:
        current = version_tuple(CORE_VERSION_FILE.read_text().strip())
    except OSError as exc:
        raise ModuleError(f"Unable to read the installed SpawnWP version: {exc}") from exc
    minimum = version_tuple(str(manifest.get("min_core_version", "0.0.0")))
    maximum = version_tuple(str(manifest.get("max_core_version", "9999.0.0")))
    if not minimum <= current <= maximum:
        raise ModuleError(
            f"Module {module_id} {version} requires SpawnWP "
            f"{manifest.get('min_core_version')} through {manifest.get('max_core_version')}",
        )
    return manifest


def _artifact_paths(source: str, temporary: Path) -> tuple[Path, Path, Path, str]:
    if source.startswith("https://"):
        archive = temporary / Path(source).name
        if not archive.name.endswith(".tar.gz"):
            raise ModuleError("Module URL must end in .tar.gz")
        prefix = source[:-7]
        pairs = (
            (source, archive),
            (prefix + ".manifest.json", temporary / (archive.name[:-7] + ".manifest.json")),
            (prefix + ".manifest.sig", temporary / (archive.name[:-7] + ".manifest.sig")),
        )
        try:
            for index, (url, target) in enumerate(pairs):
                _download(url, target, MAX_DOWNLOAD_BYTES if index == 0 else 1024 * 1024)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ModuleError(f"Unable to download module: {exc}") from exc
        return pairs[0][1], pairs[1][1], pairs[2][1], source
    archive = Path(source).resolve()
    if not archive.is_file() or not archive.name.endswith(".tar.gz"):
        raise ModuleError("Module package must be an existing .tar.gz file or HTTPS URL")
    stem = archive.name[:-7]
    manifest = archive.with_name(stem + ".manifest.json")
    signature = archive.with_name(stem + ".manifest.sig")
    if not manifest.is_file() or not signature.is_file():
        raise ModuleError("Module package is missing its adjacent manifest or signature")
    return archive, manifest, signature, str(archive)


def _run_hook(release: Path, name: str, *, force: bool = False, purge: bool = False) -> None:
    hook = release / name
    if not hook.is_file():
        return
    env = {**os.environ, "SPAWNWP_MODULE_ROOT": str(release)}
    if force:
        env["SPAWNWP_MODULE_FORCE"] = "1"
    if purge:
        env["SPAWNWP_MODULE_PURGE"] = "1"
    result = subprocess.run([str(hook)], env=env, capture_output=True, text=True)
    if result.returncode != 0:
        raise ModuleError((result.stderr or result.stdout or f"Module hook failed: {name}").strip())


def _manage_credential(action: str, module_id: str, scope: str | None = None) -> dict:
    if not COCKPIT_PYTHON.is_file() or not MODULE_API_HELPER.is_file():
        raise ModuleError("Local module credential helper is not installed")
    command = [str(COCKPIT_PYTHON), str(MODULE_API_HELPER), action, module_id]
    if action == "ensure":
        if not scope:
            raise ModuleError("Local module credential scope is missing")
        command.append(scope)
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise ModuleError((result.stderr or result.stdout or "Module credential setup failed").strip())
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ModuleError("Module credential helper returned invalid data") from exc


def install(source: str, *, expected_id: str | None = None) -> dict:
    MODULES_ROOT.mkdir(parents=True, exist_ok=True)
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="spawnwp-module-", dir=STATE_ROOT) as tmp:
        temporary = Path(tmp)
        archive, manifest_path, signature, recorded_source = _artifact_paths(source, temporary)
        verify_signature(manifest_path, signature)
        manifest = load_manifest(manifest_path, archive)
        module_id, version = manifest["id"], manifest["version"]
        if expected_id is not None and module_id != expected_id:
            raise ModuleError("Downloaded package id does not match the requested module")
        unpacked = temporary / "unpacked"
        unpacked.mkdir()
        safe_extract(archive, unpacked)
        package = unpacked / f"{module_id}-{version}"
        if not package.is_dir():
            raise ModuleError("Module archive layout is invalid")
        for entry in manifest.get("files", []):
            relative = Path(str(entry.get("path", "")))
            if relative.is_absolute() or ".." in relative.parts:
                raise ModuleError("Module manifest contains an unsafe file path")
            file = package / relative
            if not file.is_file() or sha256(file) != entry.get("sha256"):
                raise ModuleError(f"Module file verification failed: {relative}")
        destination = MODULES_ROOT / module_id / "releases" / version
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            os.replace(package, destination)
        shutil.copy2(manifest_path, destination / "module.json")
        previous = None
        previous_scope = None
        previous_state = {"status": "active"}
        current_link = MODULES_ROOT / module_id / "current"
        if current_link.is_symlink():
            previous_release = current_link.resolve()
            previous = previous_release.name
            previous_state = _read_state(module_id)
            try:
                previous_scope = json.loads((previous_release / "module.json").read_text()).get(
                    "core_api_scope",
                )
            except (OSError, json.JSONDecodeError):
                previous_scope = None
        scope = manifest.get("core_api_scope")
        if previous_scope and scope != previous_scope:
            raise ModuleError("A module update cannot change its core API scope; remove and reinstall it")
        temporary_link = current_link.with_name(".current-new")
        temporary_link.unlink(missing_ok=True)
        temporary_link.symlink_to(destination)
        os.replace(temporary_link, current_link)
        try:
            if scope:
                _manage_credential("ensure", module_id, scope)
            _run_hook(destination, "install.py")
            has_lifecycle = (destination / "activate.py").is_file() or (destination / "deactivate.py").is_file()
            desired_status = previous_state.get("status", "active") if previous else "active"
            if has_lifecycle:
                if desired_status == "disabled":
                    _run_hook(destination, "deactivate.py")
                    if scope:
                        _manage_credential("revoke", module_id)
                else:
                    _run_hook(destination, "activate.py")
                    desired_status = "active"
            state = {"id": module_id, "version": version, "previous": previous,
                     "source": recorded_source, "status": desired_status,
                     "updated_at": int(time.time()), "last_error": ""}
            _write_state(module_id, state)
        except Exception as exc:
            if not previous and scope:
                try:
                    _manage_credential("revoke", module_id)
                except Exception:
                    pass
            current_link.unlink(missing_ok=True)
            if previous:
                previous_release = destination.parent / previous
                current_link.symlink_to(previous_release)
                try:
                    _run_hook(previous_release, "install.py")
                except Exception as rollback_exc:
                    raise ModuleError(
                        f"{exc}; previous module release could not be restarted: {rollback_exc}",
                    ) from exc
            raise
        return state


def installed(module_id: str | None = None) -> list[dict]:
    if module_id and not MODULE_ID_RE.fullmatch(module_id):
        raise ModuleError("Invalid module id")
    roots = [MODULES_ROOT / module_id] if module_id else sorted(MODULES_ROOT.glob("*"))
    result = []
    for root in roots:
        manifest_path = root / "current" / "module.json"
        if not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        state = _read_state(str(manifest.get("id", root.name)))
        capabilities = _capabilities(root / "current")
        result.append({**{key: manifest.get(key) for key in (
            "id", "name", "version", "description", "admin_path", "core_api_scope",
        )}, "status": state.get("status", "active"),
        "last_error": str(state.get("last_error", ""))[:500],
        "updated_at": state.get("updated_at"),
        "capabilities": capabilities})
    return result


def update(module_id: str, source: str | None = None) -> dict:
    state_path = STATE_ROOT / module_id / "install.json"
    if not state_path.is_file():
        raise ModuleError(f"Module '{module_id}' is not installed")
    state = json.loads(state_path.read_text())
    chosen = source or state.get("source", "")
    if not chosen.startswith("https://") and source is None:
        raise ModuleError("A new --source is required for a module installed from a local file")
    return install(chosen, expected_id=module_id)


def enable(module_id: str) -> dict:
    release = _current_release(module_id)
    manifest = _manifest(release)
    if not (release / "activate.py").is_file():
        raise ModuleError(f"Module '{module_id}' does not support activation controls")
    scope = manifest.get("core_api_scope")
    if scope:
        _manage_credential("ensure", module_id, scope)
    try:
        _run_hook(release, "activate.py")
    except Exception as exc:
        if scope:
            try:
                _manage_credential("revoke", module_id)
            except Exception:
                pass
        state = _read_state(module_id)
        state.update(status="error", last_error=str(exc)[:500], updated_at=int(time.time()))
        _write_state(module_id, state)
        raise
    state = _read_state(module_id)
    state.update(status="active", last_error="", updated_at=int(time.time()), version=manifest.get("version"))
    _write_state(module_id, state)
    return state


def disable(module_id: str) -> dict:
    release = _current_release(module_id)
    manifest = _manifest(release)
    if not (release / "deactivate.py").is_file():
        raise ModuleError(f"Module '{module_id}' does not support activation controls")
    try:
        _run_hook(release, "deactivate.py")
    except Exception as exc:
        state = _read_state(module_id)
        state.update(status="error", last_error=str(exc)[:500], updated_at=int(time.time()))
        _write_state(module_id, state)
        raise
    if manifest.get("core_api_scope"):
        _manage_credential("revoke", module_id)
    state = _read_state(module_id)
    state.update(status="disabled", last_error="", updated_at=int(time.time()), version=manifest.get("version"))
    _write_state(module_id, state)
    return state


def remove(module_id: str, *, force: bool = False, purge: bool = False) -> None:
    if not MODULE_ID_RE.fullmatch(module_id):
        raise ModuleError("Invalid module id")
    root = MODULES_ROOT / module_id
    release = root / "current"
    active_release = _current_release(module_id)
    try:
        scope = json.loads((active_release / "module.json").read_text()).get("core_api_scope")
    except (OSError, json.JSONDecodeError):
        scope = None
    state = _read_state(module_id)
    if state.get("status") == "active" and (active_release / "deactivate.py").is_file():
        _run_hook(active_release, "deactivate.py", force=force)
    _run_hook(active_release, "uninstall.py", force=force, purge=purge)
    if scope:
        _manage_credential("revoke", module_id)
    release.unlink(missing_ok=True)
    # Keep install.json as an audit trail, but mark the module as removed.
    state.update(status="removed", updated_at=int(time.time()), last_error="")
    _write_state(module_id, state)
