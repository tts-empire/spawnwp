#!/usr/bin/env python3
"""Build and sign deterministic SpawnWP GitHub Release assets."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEMVER_PARTS = 3


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def add_entry(entries: list[dict], package: Path, source: Path, package_path: str,
              target_root: str, target: str, mode: str = "0644") -> None:
    destination = package / package_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    os.chmod(destination, int(mode, 8))
    entries.append({
        "source": package_path,
        "target_root": target_root,
        "target": target,
        "mode": mode,
        "sha256": sha256(destination),
    })


def normalize_tar(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    info.mtime = 0
    return info


def build_deploy_plugin_zip(destination: Path) -> None:
    """Build a deterministic zip of the SpawnWP Deploy plugin so the cockpit can
    install it on opt-in sites. Same file set as plugins/spawnwp-deploy/bin/
    build-release.sh; fixed timestamps keep the release reproducible. The zip is
    covered by the core release signature, so no separate plugin signature ships."""
    import zipfile

    plugin = ROOT / "plugins" / "spawnwp-deploy"
    members: list[Path] = [plugin / "spawnwp-deploy.php", plugin / "README.md"]
    for sub in ("src", "recovery"):
        members += sorted(p for p in (plugin / sub).rglob("*") if p.is_file())
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in members:
            arcname = "spawnwp-deploy/" + str(path.relative_to(plugin))
            info = zipfile.ZipInfo(arcname, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default=(ROOT / "VERSION").read_text().strip())
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    if len(args.version.split(".")) != SEMVER_PARTS or not all(part.isdigit() for part in args.version.split(".")):
        parser.error("--version must be MAJOR.MINOR.PATCH")
    if not args.key.is_file():
        parser.error("signing key not found")

    managed = json.loads((ROOT / "updater/managed-files.json").read_text())
    args.output.mkdir(parents=True, exist_ok=True)
    prefix = f"spawnwp-{args.version}"
    with tempfile.TemporaryDirectory(prefix="spawnwp-release-") as temporary:
        package = Path(temporary) / prefix
        entries: list[dict] = []
        for relative in managed["cockpit"]:
            source = ROOT / "runtime" / relative
            target = (relative if relative in {"app.py", "auth.py", "ingest.py", "machine_auth.py", "requirements.txt"}
                      else f"static/{relative}")
            add_entry(entries, package, source, f"payload/cockpit/{target}", "cockpit", target)
        for relative in managed["runtime"]:
            mode = "0755" if relative.startswith("scripts/") else "0644"
            add_entry(entries, package, ROOT / "runtime" / relative,
                      f"payload/runtime/{relative}", "runtime", relative, mode)
        for relative in managed["installer"]:
            mode = "0755" if relative in {
                "migrations/remove-legacy-access.py",
                "migrations/remove-obsolete-network-gate.py",
                "migrations/install-dashboard-update-service.py",
                "migrations/update-docker-prune-service.py",
                "migrations/install-image-gc-units.py",
                "migrations/install-site-expiry-units.py",
                "migrations/add-ingest-nginx-location.py",
                "telemetry.py",
            } else "0644"
            add_entry(entries, package, ROOT / "installer" / relative,
                      f"payload/lib/installer/{relative}", "lib", f"installer/{relative}", mode)
        add_entry(entries, package, ROOT / "install.sh", "payload/lib/installer/install.sh",
                  "lib", "installer/install.sh", "0755")
        add_entry(entries, package, ROOT / "updater/spawnwp", "payload/bin/spawnwp",
                  "bin", "spawnwp", "0755")
        add_entry(entries, package, ROOT / "updater/release-public.pem",
                  "payload/lib/release-public.pem", "lib", "release-public.pem")

        # The SpawnWP Deploy plugin, bundled so the cockpit can install it on
        # sites created with the opt-in checkbox. Generated (not a repo file),
        # then added as a normal runtime asset covered by the release signature.
        deploy_zip = Path(temporary) / "spawnwp-deploy.zip"
        build_deploy_plugin_zip(deploy_zip)
        add_entry(entries, package, deploy_zip, "payload/runtime/assets/spawnwp-deploy.zip",
                  "runtime", "assets/spawnwp-deploy.zip", "0644")

        archive = args.output / f"{prefix}.tar.gz"
        with archive.open("wb") as raw:
            import gzip
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped:
                with tarfile.open(fileobj=zipped, mode="w") as tar:
                    tar.add(package, arcname=prefix, filter=normalize_tar)

        manifest = {
            "schema": 1,
            "version": args.version,
            "min_updater_version": "0.1.0",
            "archive": archive.name,
            "archive_sha256": sha256(archive),
            "migrations": [
                "installer/migrations/remove-legacy-access.py",
                "installer/migrations/remove-obsolete-network-gate.py",
                "installer/migrations/install-dashboard-update-service.py",
                "installer/migrations/update-docker-prune-service.py",
                "installer/migrations/install-image-gc-units.py",
                "installer/migrations/install-site-expiry-units.py",
                "installer/migrations/add-ingest-nginx-location.py",
            ],
            "files": entries,
        }
        manifest_path = args.output / f"{prefix}.manifest.json"
        manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n")
        signature = args.output / f"{prefix}.manifest.sig"
        subprocess.run([
            "openssl", "pkeyutl", "-sign", "-inkey", str(args.key), "-rawin",
            "-in", str(manifest_path), "-out", str(signature),
        ], check=True)
        (args.output / f"{prefix}.sha256").write_text(
            f"{sha256(archive)}  {archive.name}\n"
            f"{sha256(manifest_path)}  {manifest_path.name}\n"
            f"{sha256(signature)}  {signature.name}\n"
        )
    print(f"Built {prefix} in {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
