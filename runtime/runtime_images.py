"""Prepare verified PHP images and retain immutable references for site selection."""
from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Callable

from php_security import image_report

PHP_IMAGE_REPO = "wp-dev-php"
SERIES = {"7.4", "8.2", "8.3", "8.4", "8.5"}
TAG_RE = re.compile(r"^([0-9]+\.[0-9]+)(?:-wp([0-9.]+))?(?:-trixie-([a-f0-9]{12,64}))?$")


def suffix(wp: str) -> str:
    if wp == "latest":
        return ""
    if not re.fullmatch(r"\d+(?:\.\d+){0,2}", wp):
        raise ValueError("Invalid WordPress version")
    return "-wp" + wp


def alias(series: str, wp: str = "latest") -> str:
    if series not in SERIES:
        raise ValueError("Unsupported PHP version")
    return f"{PHP_IMAGE_REPO}:{series}{suffix(wp)}"


def selected(env: dict[str, str]) -> str:
    return env.get("PHP_IMAGE_REF") or f"wp-dev-php:{env.get('PHP_VERSION', '8.3')}{env.get('WP_IMAGE_SUFFIX', '')}"


def docker(*args: str) -> str:
    return subprocess.check_output(["docker", *args], text=True).strip()


def preserve(reference: str, series: str, wp: str = "latest") -> str:
    """Tag the exact current image, before a floating tag can move."""
    identity = docker("image", "inspect", "-f", "{{.Id}}", reference)
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", identity):
        raise RuntimeError("Invalid Docker image identity")
    tag = alias(series, wp) + "-trixie-" + identity[7:19]
    # The suffix is an immutable identity, not evidence of its OS/security status.
    docker("tag", identity, tag)
    return tag


def prepare(project: Path, series: str, wp: str = "latest", *, refresh: bool = False,
            log: Callable[[str], None] = print) -> str:
    reference = alias(series, wp)
    context = project / "docker" / "php"
    recipe = subprocess.check_output(
        ["bash", "scripts/lib-image.sh", "hash", wp], cwd=project, text=True,
    ).strip()
    lock_root = Path(os.environ.get("SPAWNWP_IMAGE_LOCK_ROOT", "/run/lock"))
    lock_root.mkdir(parents=True, exist_ok=True)
    with (lock_root / (reference.replace(":", "-") + ".lock")).open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            cached = json.loads(docker("image", "inspect", reference))[0]
        except (subprocess.CalledProcessError, ValueError, IndexError):
            cached = {}
        labels = cached.get("Config", {}).get("Labels") or {}
        if not refresh and labels.get("org.spawnwp.context-hash") == recipe:
            report = image_report(cached["Id"])
            if report["status"] == "patched" and report.get("os_version") == "13":
                log("Reusing the verified prepared image")
                return preserve(cached["Id"], series, wp)
        manifest = json.loads((context / "runtime-lock.json").read_text())
        base = manifest["images"]["legacy" if series == "7.4" else series]
        if refresh:
            upstream = base.split("@", 1)[0]
            descriptor = json.loads(docker("buildx", "imagetools", "inspect", upstream,
                                           "--format", "{{json .Manifest}}"))
            digest = descriptor.get("digest") or descriptor.get("Digest")
            if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest or ""):
                raise RuntimeError("Could not resolve the upstream image digest")
            base = upstream + "@" + digest
        candidate = f"wp-dev-php-build:{series}-{uuid.uuid4().hex}"
        args = ["docker", "build", "--pull", "--progress=plain", "-t", candidate,
                "-f", str(context / ("Dockerfile.legacy" if series == "7.4" else "Dockerfile")),
                "--build-arg", f"PHP_VERSION={series}",
                "--build-arg", f"PHP_BASE_IMAGE={base}",
                "--build-arg", f"WORDPRESS_VERSION={wp}",
                "--build-arg", f"SPAWNWP_CONTEXT_HASH={recipe}", str(context)]
        log(f"First use of PHP {series} or changed recipe: preparing a verified image")
        try:
            process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            assert process.stdout is not None
            for line in process.stdout:
                log(line.rstrip())
            if process.wait():
                raise RuntimeError("The PHP image build failed")
            current_recipe = subprocess.check_output(
                ["bash", "scripts/lib-image.sh", "hash", wp], cwd=project, text=True,
            ).strip()
            if current_recipe != recipe:
                raise RuntimeError("Build inputs changed during preparation; candidate not promoted")
            report = image_report(candidate)
            if report["status"] != "patched" or report.get("os_version") != "13":
                raise RuntimeError("Candidate rejected: Debian 13 and PHP patch provenance must be verified")
            immutable = preserve(report["image_id"], series, wp)
            docker("tag", report["image_id"], reference)
            return immutable
        finally:
            subprocess.run(["docker", "image", "rm", candidate], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, check=False)


def write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(content)
        os.chmod(temporary, path.stat().st_mode & 0o777 if path.exists() else 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def references(projects: list[Path]) -> dict[str, list[str]]:
    """Protect selected images and retained rollback images, including Down sites."""
    import time
    result: dict[str, list[str]] = {}
    for project in projects:
        env_path = project / ".env"
        if not env_path.is_file():
            continue
        env = dict(line.split("=", 1) for line in env_path.read_text().splitlines()
                   if "=" in line and not line.startswith("#"))
        ref = selected(env)
        if ref.startswith("wp-dev-php:"):
            result.setdefault(ref.split(":", 1)[1], []).append(project.name)
        for name in ("runtime-rollback.json", "runtime-history.json"):
            path = project / ".spawnwp" / name
            if not path.exists():
                continue
            records = json.loads(path.read_text())
            if isinstance(records, dict):
                records = [records]
            for record in records:
                if name == "runtime-rollback.json" or record.get("retain_until", 0) > time.time():
                    ref = record.get("previous_ref", "")
                    if ref.startswith("wp-dev-php:"):
                        result.setdefault(ref.split(":", 1)[1], []).append(project.name + " (rollback)")
    return result
