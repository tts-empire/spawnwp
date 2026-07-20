#!/usr/bin/env python3
"""Synchronize the signed SpawnWP Deploy mirror with WordPress.org stable."""

from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


SLUG = "spawnwp-deploy"
API_URL = (
    "https://api.wordpress.org/plugins/info/1.2/"
    "?action=plugin_information&request%5Bslug%5D=spawnwp-deploy"
)
DOWNLOAD_HOST = "downloads.wordpress.org"
MAX_API_BYTES = 1024 * 1024
MAX_ZIP_BYTES = 256 * 1024 * 1024
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
HEADER_VERSION_RE = re.compile(r"^\s*\*\s*Version:\s*(\S+)\s*$", re.MULTILINE)
CONSTANT_VERSION_RE = re.compile(
    r"define\(\s*'SPAWNWP_DEPLOY_VERSION'\s*,\s*'([^']+)'\s*\)"
)
STABLE_TAG_RE = re.compile(r"^Stable tag:\s*(\S+)\s*$", re.MULTILINE | re.IGNORECASE)


class SyncError(RuntimeError):
    """A validation or synchronization failure."""


@dataclass(frozen=True)
class Release:
    version: str
    download_url: str

    @property
    def filename(self) -> str:
        return f"{SLUG}-{self.version}.zip"


def version_key(version: str) -> tuple[int, int, int]:
    if not VERSION_RE.fullmatch(version):
        raise SyncError(f"not a stable semantic version: {version!r}")
    return tuple(int(part) for part in version.split("."))  # type: ignore[return-value]


def read_limited(response, maximum: int) -> bytes:
    declared = response.headers.get("Content-Length")
    if declared and int(declared) > maximum:
        raise SyncError("remote response is larger than the allowed maximum")
    data = response.read(maximum + 1)
    if len(data) > maximum:
        raise SyncError("remote response is larger than the allowed maximum")
    return data


def request_bytes(url: str, maximum: int, timeout: int) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "SpawnWP-WPOrg-Sync/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return read_limited(response, maximum)
    except (OSError, ValueError) as exc:
        raise SyncError(f"request failed for {url}: {exc}") from exc


def fetch_release(api_url: str = API_URL) -> Release:
    try:
        payload = json.loads(request_bytes(api_url, MAX_API_BYTES, 15))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SyncError("WordPress.org returned invalid plugin metadata") from exc
    if not isinstance(payload, dict) or payload.get("slug") != SLUG:
        raise SyncError("WordPress.org metadata has an unexpected plugin slug")
    version = payload.get("version")
    download_url = payload.get("download_link")
    if not isinstance(version, str) or not isinstance(download_url, str):
        raise SyncError("WordPress.org metadata is missing version or download_link")
    version_key(version)
    parsed = urllib.parse.urlparse(download_url)
    expected_path = f"/plugin/{SLUG}.{version}.zip"
    if (
        parsed.scheme != "https"
        or parsed.hostname != DOWNLOAD_HOST
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != expected_path
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise SyncError("WordPress.org metadata contains an untrusted download URL")
    return Release(version=version, download_url=download_url)


def download_release(release: Release, destination: Path) -> None:
    destination.write_bytes(request_bytes(release.download_url, MAX_ZIP_BYTES, 60))


def zip_member_is_symlink(info: zipfile.ZipInfo) -> bool:
    return stat.S_ISLNK((info.external_attr >> 16) & 0xFFFF)


def validate_zip(archive: Path, release: Release) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(archive) as package:
            seen: set[str] = set()
            for info in package.infolist():
                name = info.filename
                path = PurePosixPath(name)
                if (
                    not name
                    or "\\" in name
                    or path.is_absolute()
                    or ".." in path.parts
                    or not path.parts
                    or path.parts[0] != SLUG
                    or zip_member_is_symlink(info)
                    or name in seen
                ):
                    raise SyncError(f"unsafe ZIP member: {name!r}")
                seen.add(name)
                if info.is_dir():
                    continue
                files[name] = package.read(info)
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        if isinstance(exc, SyncError):
            raise
        raise SyncError(f"invalid plugin ZIP: {exc}") from exc

    main_name = f"{SLUG}/{SLUG}.php"
    readme_name = f"{SLUG}/readme.txt"
    if main_name not in files or readme_name not in files:
        raise SyncError("plugin ZIP is missing its main file or readme.txt")
    try:
        main = files[main_name].decode("utf-8")
        readme = files[readme_name].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SyncError("plugin headers are not valid UTF-8") from exc
    header = HEADER_VERSION_RE.search(main)
    constant = CONSTANT_VERSION_RE.search(main)
    stable_tag = STABLE_TAG_RE.search(readme)
    if not header or header.group(1) != release.version:
        raise SyncError("plugin header version does not match WordPress.org metadata")
    if not constant or constant.group(1) != release.version:
        raise SyncError("plugin version constant does not match WordPress.org metadata")
    if not stable_tag or stable_tag.group(1) != release.version:
        raise SyncError("Stable tag does not match WordPress.org metadata")
    return files


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_checksum(path: Path, archive: Path, digest: str) -> None:
    path.write_text(f"{digest}  {archive.name}\n", encoding="ascii")


def sign_checksum(checksum: Path, signature: Path, private_key: Path) -> None:
    try:
        raw = subprocess.run(
            [
                "openssl",
                "pkeyutl",
                "-sign",
                "-inkey",
                str(private_key),
                "-rawin",
                "-in",
                str(checksum),
            ],
            check=True,
            capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SyncError("could not sign the mirrored plugin release") from exc
    signature.write_bytes(base64.b64encode(raw) + b"\n")


def verify_signature(checksum: Path, signature: Path, public_key: Path) -> None:
    try:
        raw = base64.b64decode(signature.read_bytes().strip(), validate=True)
    except (OSError, ValueError) as exc:
        raise SyncError("plugin signature is not valid base64") from exc
    with tempfile.NamedTemporaryFile() as decoded:
        decoded.write(raw)
        decoded.flush()
        try:
            subprocess.run(
                [
                    "openssl",
                    "pkeyutl",
                    "-verify",
                    "-rawin",
                    "-pubin",
                    "-inkey",
                    str(public_key),
                    "-in",
                    str(checksum),
                    "-sigfile",
                    decoded.name,
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise SyncError("plugin release signature verification failed") from exc


def read_latest(target: Path) -> dict:
    path = target / "latest.json"
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def assert_no_downgrade(release: Release, target: Path) -> None:
    current = read_latest(target).get("version")
    if isinstance(current, str) and VERSION_RE.fullmatch(current):
        if version_key(release.version) < version_key(current):
            raise SyncError(f"refusing to downgrade mirror from {current} to {release.version}")


def expected_metadata(release: Release, digest: str) -> dict[str, str]:
    return {"version": release.version, "zip": release.filename, "sha256": digest}


def check_mirror(release: Release, remote_zip: Path, target: Path, public_key: Path) -> None:
    digest = sha256(remote_zip)
    metadata = read_latest(target)
    if metadata != expected_metadata(release, digest):
        raise SyncError("latest.json does not match the WordPress.org stable release")
    local_zip = target / release.filename
    checksum = target / f"{release.filename}.sha256"
    signature = target / f"{release.filename}.sig"
    if not local_zip.is_file() or sha256(local_zip) != digest:
        raise SyncError("mirrored ZIP differs from WordPress.org")
    expected_checksum = f"{digest}  {release.filename}\n"
    if not checksum.is_file() or checksum.read_text(encoding="ascii") != expected_checksum:
        raise SyncError("mirrored checksum is missing or incorrect")
    if not signature.is_file():
        raise SyncError("mirrored signature is missing")
    verify_signature(checksum, signature, public_key)


def archive_dev_releases(target: Path, archive_dir: Path) -> int:
    candidates = sorted(target.glob(f"{SLUG}-*-dev.zip*"))
    if not candidates:
        return 0
    archive_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(archive_dir, 0o700)
    for source in candidates:
        destination = archive_dir / source.name
        shutil.move(str(source), destination)
        os.chmod(destination, 0o600)
    return len(candidates)


def publish_mirror(
    release: Release,
    target: Path,
    private_key: Path,
    public_key: Path,
    archive_dir: Path,
) -> tuple[str, bool, int]:
    target.mkdir(parents=True, exist_ok=True)
    if not private_key.is_file() or not public_key.is_file():
        raise SyncError("plugin signing key is unavailable")
    assert_no_downgrade(release, target)
    with tempfile.TemporaryDirectory(prefix=".wporg-sync-", dir=target) as temporary:
        stage = Path(temporary)
        plugin_zip = stage / release.filename
        download_release(release, plugin_zip)
        validate_zip(plugin_zip, release)
        digest = sha256(plugin_zip)
        checksum = stage / f"{release.filename}.sha256"
        signature = stage / f"{release.filename}.sig"
        metadata = stage / "latest.json"
        write_checksum(checksum, plugin_zip, digest)
        sign_checksum(checksum, signature, private_key)
        verify_signature(checksum, signature, public_key)
        metadata.write_text(
            json.dumps(expected_metadata(release, digest), separators=(",", ":")) + "\n",
            encoding="utf-8",
        )

        changed = not (target / release.filename).is_file() or sha256(target / release.filename) != digest
        for source in (plugin_zip, checksum, signature):
            os.chmod(source, 0o644)
            os.replace(source, target / source.name)
        os.chmod(metadata, 0o644)
        os.replace(metadata, target / "latest.json")

    archived = archive_dev_releases(target, archive_dir)
    return digest, changed, archived


def check_source(files: dict[str, bytes], source: Path) -> None:
    expected: dict[str, bytes] = {}
    for relative in (Path(f"{SLUG}.php"), Path("readme.txt")):
        expected[f"{SLUG}/{relative.as_posix()}"] = (source / relative).read_bytes()
    for directory in ("assets", "recovery", "src"):
        for path in sorted((source / directory).rglob("*")):
            if path.is_file():
                relative = path.relative_to(source).as_posix()
                expected[f"{SLUG}/{relative}"] = path.read_bytes()
    if files != expected:
        missing = sorted(set(files) - set(expected))
        extra = sorted(set(expected) - set(files))
        changed = sorted(name for name in set(files) & set(expected) if files[name] != expected[name])
        raise SyncError(f"Git source differs from wp.org; missing={missing}, extra={extra}, changed={changed}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="check the live signed mirror")
    mode.add_argument("--publish", action="store_true", help="publish the official stable to the mirror")
    mode.add_argument("--check-source", type=Path, help="compare a Git source tree with wp.org")
    parser.add_argument("--api-url", default=API_URL)
    parser.add_argument("--target", type=Path, default=Path("/var/www/spawnwp-downloads"))
    parser.add_argument(
        "--private-key", type=Path, default=Path("/root/.spawnwp/deploy-release-ed25519.pem")
    )
    parser.add_argument(
        "--public-key", type=Path, default=Path("/var/www/spawnwp-downloads/release-public.pem")
    )
    parser.add_argument(
        "--archive-dir", type=Path, default=Path("/var/backups/spawnwp-plugin-previews")
    )
    parser.add_argument("--lock-file", type=Path, default=Path("/var/lock/spawnwp-plugin-sync.lock"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.lock_file.parent.mkdir(parents=True, exist_ok=True)
    with args.lock_file.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        release = fetch_release(args.api_url)
        with tempfile.TemporaryDirectory(prefix="spawnwp-wporg-check-") as temporary:
            remote_zip = Path(temporary) / release.filename
            download_release(release, remote_zip)
            files = validate_zip(remote_zip, release)
            if args.check_source:
                check_source(files, args.check_source)
                print(f"source matches WordPress.org {release.version}")
                return 0
            if args.check:
                check_mirror(release, remote_zip, args.target, args.public_key)
                print(f"mirror matches WordPress.org {release.version} ({sha256(remote_zip)})")
                return 0

        digest, changed, archived = publish_mirror(
            release, args.target, args.private_key, args.public_key, args.archive_dir
        )
        action = "published" if changed else "verified"
        print(f"{action} WordPress.org {release.version} ({digest}); archived_dev_files={archived}")
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SyncError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
