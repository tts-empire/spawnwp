import asyncio
import base64
import hashlib
import json
import os
import posixpath
import re
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from auth import initialize as initialize_auth
from auth import is_enrolled, login_page, rate_limit, router as auth_router, session as auth_session, valid_csrf
from ingest import router as ingest_router, spawnwp_version
from module_catalog import CatalogError, load as load_module_catalog

@asynccontextmanager
async def lifespan(_app: FastAPI):
    initialize_auth()
    yield


app = FastAPI(docs_url=None, redoc_url=None, lifespan=lifespan)
app.include_router(auth_router)
app.include_router(ingest_router)

# POST endpoints that require a *recent* passkey re-auth (step-up), beyond a
# valid session. Static high-impact actions plus the per-site file-manager
# writes (whose paths carry a {project} segment, matched by regex).
DESTRUCTIVE_PATHS = {"/api/destroy", "/api/restore", "/api/php-switch", "/api/update/apply",
                     "/api/images/delete", "/api/images/refresh", "/api/blueprint-pairings",
                     "/api/snapshots/delete"}
FILE_WRITE_RE = re.compile(r"^/api/files/[^/]+/(write|upload|delete|rename|mkdir|unzip)$")
MODULE_MUTATION_RE = re.compile(r"^/api/modules(?:/[^/]+)?(?:/(?:enable|disable|update))?$|^/api/modules/catalog/install$")


def requires_recent_auth(path: str) -> bool:
    return path in DESTRUCTIVE_PATHS or bool(FILE_WRITE_RE.match(path)) or bool(MODULE_MUTATION_RE.match(path))


@app.middleware("http")
async def application_authentication(request: Request, call_next):
    path = request.url.path
    mutation = path.startswith("/api/") and request.method in {"POST", "PUT", "PATCH", "DELETE"}
    public = path in {
        "/login", "/api/version", "/api/auth/state", "/api/auth/setup/start",
        "/api/auth/setup/finish", "/api/auth/passkey/start", "/api/auth/passkey/finish",
        "/api/auth/fallback",
    } or path.startswith("/api/ingest/") or path == "/api/provision" or path.startswith("/api/provision/")
    # Signed-request auth for public machine paths lives in ingest.py/provision.py.
    active = None if public else auth_session(request)
    response = None
    # Authentication endpoints already have tighter, action-specific limits.
    # Private endpoints are counted only after session auth, so an unauthenticated
    # caller cannot exhaust the bucket used by a legitimate cockpit administrator.
    if mutation and not path.startswith("/api/auth/") and (public or active):
        try:
            rate_limit(request, "api_mutation", limit=60)
        except HTTPException as exc:
            response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
            response.headers["Retry-After"] = "300"
    if response is not None:
        pass
    elif not public and not active:
        if path.startswith("/api/"):
            response = JSONResponse({"detail": "Authentication required"}, status_code=401)
        else:
            response = RedirectResponse("/login", status_code=303)
    elif active and request.method in {"POST", "PUT", "PATCH", "DELETE"} and not valid_csrf(request, active):
        response = JSONResponse({"detail": "Invalid CSRF token"}, status_code=403)
    elif active and request.method in {"POST", "PUT", "PATCH", "DELETE"} and requires_recent_auth(path) and int(__import__("time").time()) - active["recent_auth"] > 600:
        response = JSONResponse({"detail": "Recent authentication required; sign out and sign in again"}, status_code=403)
    else:
        response = await call_next(request)
    # Security headers on every response — including the fail-closed redirect and
    # the 401/403 early returns above, not only the ones that reach call_next.
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), publickey-credentials-get=(self), publickey-credentials-create=(self)")
    # One policy for every response. The cockpit UI relies on inline event
    # handlers and inline styles, so script/style keep 'unsafe-inline'; the rest
    # is locked down to same-origin (no external scripts, connections or frames).
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; connect-src 'self'; object-src 'none'; "
        "base-uri 'none'; frame-ancestors 'none'",
    )
    return response

# ── Constants ──────────────────────────────────────────────────────────────────

PROJECTS_ROOT = Path("/srv")
PRIMARY_PROJECT = PROJECTS_ROOT / "wp-dev"
DOCKER_DATA_ROOT = Path(os.environ.get("SPAWNWP_DOCKER_DATA_ROOT", "/var/lib/docker"))
CONFIG_ENV = Path(os.environ.get("SPAWNWP_CONFIG_ENV", "/etc/spawnwp/config.env"))
TEMPLATE_MARKER = PRIMARY_PROJECT / ".spawnwp" / "template-only"
BLUEPRINT_TOOL = PRIMARY_PROJECT / "scripts" / "blueprint.py"
PHP_SWITCH_TOOL = PRIMARY_PROJECT / "scripts" / "php-switch-progress.py"
SPAWNWP_CLI = Path("/usr/local/bin/spawnwp")
UPDATE_SERVICE = "spawnwp-update.service"

# Every project dir contains a compose.yaml and a Makefile
def is_project(p: Path) -> bool:
    return p.is_dir() and (p / "compose.yaml").exists() and (p / "Makefile").exists()

def get_projects() -> list[Path]:
    return sorted([
        p for p in PROJECTS_ROOT.iterdir()
        if is_project(p) and not (p == PRIMARY_PROJECT and TEMPLATE_MARKER.is_file())
    ])


def blueprint_catalog() -> dict:
    try:
        result = subprocess.run(
            ["python3", str(BLUEPRINT_TOOL), "list"],
            capture_output=True, text=True, cwd=PRIMARY_PROJECT,
        )
    except OSError as exc:
        # A fresh CI/test checkout (or a partially recovered host) may not have
        # the primary project yet. Return the same controlled API error used for
        # a failed blueprint command instead of leaking FileNotFoundError.
        raise HTTPException(500, "Primary SpawnWP project is unavailable") from exc
    if result.returncode != 0:
        raise HTTPException(500, result.stderr.strip() or "Unable to load blueprints")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise HTTPException(500, "Blueprint catalog returned invalid JSON") from exc


def validate_blueprint_choice(blueprint_id: str, php_version: str | None,
                              wordpress_version: str | None = None) -> None:
    cmd = ["python3", str(BLUEPRINT_TOOL), "resolve", blueprint_id]
    if php_version:
        cmd.extend(["--php", php_version])
    if wordpress_version:
        cmd.extend(["--wordpress", wordpress_version])
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=PRIMARY_PROJECT)
    if result.returncode != 0:
        raise HTTPException(400, result.stderr.strip().removeprefix("ERROR: ").strip())

SLUG_RE = re.compile(r'^[a-z0-9][a-z0-9\-]{0,30}$')
WP_USERNAME_RE = re.compile(r'^[a-z0-9][a-z0-9._\-]{0,59}$')
WP_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
SNAP_RE = re.compile(r'^\d{8}-\d{6}$')   # timestamp snapshot: YYYYMMDD-HHMMSS
# Manage-dashboard group label: free text, but no '=' or newline (would corrupt
# the site's .env) and no leading separator.
GROUP_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9 ._\-]{0,31}$')

# Group colours are a property of the group, not of a site: keeping them in one
# map (rather than per-site) is what stops the same group showing three colours.
GROUP_COLORS_FILE = Path(os.environ.get("SPAWNWP_GROUP_COLORS", "/var/lib/spawnwp/group-colors.json"))
GROUP_COLOR_MAX = 6   # palette size; 0 = no colour


def group_colors() -> dict[str, int]:
    """group label -> palette index (1..6). A missing or corrupt file means no colours."""
    try:
        data = json.loads(GROUP_COLORS_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(label): int(color)
        for label, color in data.items()
        if isinstance(color, int) and 1 <= color <= GROUP_COLOR_MAX
    }

# ── Helpers ────────────────────────────────────────────────────────────────────

def run(cmd: list[str], cwd: Path) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    return (result.stdout + result.stderr).strip()

async def stream_command(cmd: list[str], cwd: Path, env: dict | None = None) -> AsyncIterator[str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=cwd,
        env={**os.environ, **env} if env else None,
    )
    assert proc.stdout
    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        decoded = line.decode(errors="replace").rstrip()
        if decoded.startswith("::spawnwp-event::"):
            try:
                event = json.loads(decoded.removeprefix("::spawnwp-event::"))
            except json.JSONDecodeError:
                event = {"type": "log", "line": decoded}
            yield f"data: {json.dumps(event)}\n\n"
        else:
            yield f"data: {json.dumps(decoded)}\n\n"
    await proc.wait()
    rc = proc.returncode
    yield f"data: {json.dumps(f'__EXIT__{rc}')}\n\n"

def sse_response(cmd: list[str], cwd: Path, env: dict | None = None) -> StreamingResponse:
    return StreamingResponse(
        stream_command(cmd, cwd, env),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

def resolve_project(name: str) -> Path:
    path = PROJECTS_ROOT / name
    if not is_project(path):
        raise HTTPException(404, f"Project '{name}' not found")
    return path

# ── Guardrail: system "stress" state ─────────────────────────────────────────────
# During an image build (php-switch/new-project on an uncached version) the CPU
# hits 100% and the other containers' healthchecks can flap: sensitive actions
# fired at that moment produce transient errors. We detect the state and (a)
# expose it to the UI, (b) block mutating actions server-side.

_SHELLS = {"bash", "sh", "dash", "zsh", "python", "python3", "pgrep", "grep"}

def _build_in_progress() -> bool:
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            raw = (p / "cmdline").read_bytes()
        except OSError:
            continue
        if not raw:
            continue
        cmd = raw.replace(b"\x00", b" ").decode(errors="replace")
        # 'buildkit/executor' = an active RUN step of a build (very specific)
        if "buildkit/executor" in cmd:
            return True
        # 'compose build' is present for the whole build, but we accept it only
        # if it is NOT a shell/script that merely mentions the string by chance
        if "compose build" in cmd:
            argv0 = raw.split(b"\x00", 1)[0].rsplit(b"/", 1)[-1].decode(errors="replace")
            if argv0 not in _SHELLS:
                return True
    return False

def system_status() -> dict:
    building = _build_in_progress()
    try:
        load1 = os.getloadavg()[0]
    except OSError:
        load1 = 0.0
    ncpu = os.cpu_count() or 1
    high_load = load1 > ncpu * 2.0
    busy = building or high_load
    if building:
        reason = "image build in progress"
    elif high_load:
        reason = "high CPU load"
    else:
        reason = ""
    return {"busy": busy, "building": building, "high_load": high_load,
            "reason": reason, "load1": round(load1, 2), "ncpu": ncpu}

def guard_not_busy():
    """Block mutating actions while an image build is in progress."""
    if _build_in_progress():
        raise HTTPException(
            409,
            "System under load: image build in progress. Action blocked to "
            "avoid instability. Try again shortly.",
        )


# A cold PHP image build needs about 1.9 GiB on the measured production host.
# Keep 3 GiB free to cover that image, one site and operational headroom.
MIN_CAPACITY_FREE_BYTES = 3 * 1024**3


def _capacity_roots() -> list[Path]:
    """Return distinct existing filesystems used by sites and Docker data."""
    roots = [PROJECTS_ROOT]
    if DOCKER_DATA_ROOT.exists():
        try:
            if DOCKER_DATA_ROOT.stat().st_dev != PROJECTS_ROOT.stat().st_dev:
                roots.append(DOCKER_DATA_ROOT)
        except OSError:
            roots.append(DOCKER_DATA_ROOT)
    return roots


def guard_capacity() -> None:
    """Reject site creation before it can exhaust disk or the configured quota."""
    for root in _capacity_roots():
        try:
            free = shutil.disk_usage(root).free
        except OSError as exc:
            raise HTTPException(507, f"Unable to determine free space on {root}") from exc
        if free < MIN_CAPACITY_FREE_BYTES:
            free_gib = free / 1024**3
            raise HTTPException(
                507,
                f"Insufficient disk space on {root}: {free_gib:.1f} GiB free; "
                "at least 3.0 GiB is required to create a site",
            )

    raw_limit = _config_env_get("SPAWNWP_MAX_SITES", "0").strip()
    try:
        max_sites = int(raw_limit or "0")
    except ValueError as exc:
        raise HTTPException(500, "Invalid SPAWNWP_MAX_SITES configuration") from exc
    if max_sites < 0:
        raise HTTPException(500, "Invalid SPAWNWP_MAX_SITES configuration")
    if max_sites and len(get_projects()) >= max_sites:
        raise HTTPException(
            409,
            f"Site capacity reached: SPAWNWP_MAX_SITES is {max_sites}",
        )


def random_project_name(prefix: str = "site", attempts: int = 5) -> str:
    """Generate an unguessable path-safe project name, retrying collisions."""
    if not SLUG_RE.match(prefix) or len(prefix) > 18:
        raise ValueError("Invalid random project prefix")
    for _ in range(attempts):
        name = f"{prefix}-{secrets.token_hex(6)}"
        if not is_project(PROJECTS_ROOT / name):
            return name
    raise HTTPException(503, "Unable to allocate a unique project name; retry shortly")

# ── API ────────────────────────────────────────────────────────────────────────

@app.get("/api/version")
def version_info():
    return {"version": spawnwp_version()}


@app.get("/api/platform")
def platform_info():
    values = {}
    config = Path("/etc/spawnwp/config.env")
    if config.is_file():
        for line in config.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                values[key] = value
    domain = values.get("DOMAIN", "")
    return {"domain": domain, "sites_url": f"https://{domain}" if domain else ""}


@app.get("/api/update-status")
def update_status():
    if not SPAWNWP_CLI.is_file():
        return {"current": version_info()["version"], "available": False,
                "error": "Updater is not installed"}
    try:
        result = subprocess.run(
            [str(SPAWNWP_CLI), "update", "--check", "--json"],
            capture_output=True, text=True, timeout=12,
        )
        payload = json.loads(result.stdout)
        if result.returncode != 0 and "error" not in payload:
            payload["error"] = result.stderr.strip() or "Update check failed"
        return payload
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        return {"current": version_info()["version"], "available": False,
                "error": str(exc)}


@app.post("/api/update/apply")
def apply_update():
    guard_not_busy()
    if not Path(f"/etc/systemd/system/{UPDATE_SERVICE}").is_file():
        raise HTTPException(503, "Dashboard update service is not installed")
    active = subprocess.run(
        ["systemctl", "is-active", "--quiet", UPDATE_SERVICE],
        capture_output=True,
    )
    if active.returncode == 0:
        raise HTTPException(409, "A SpawnWP update is already running")
    subprocess.run(["systemctl", "reset-failed", UPDATE_SERVICE], capture_output=True)
    result = subprocess.run(
        ["systemctl", "start", "--no-block", UPDATE_SERVICE],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise HTTPException(500, result.stderr.strip() or "Unable to start update")
    return {"started": True, "service": UPDATE_SERVICE}


@app.get("/api/update/job")
def update_job():
    result = subprocess.run(
        ["systemctl", "show", UPDATE_SERVICE, "--property=ActiveState",
         "--property=SubState", "--property=Result", "--property=ExecMainStatus"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return {"state": "unavailable", "error": result.stderr.strip()}
    values = dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )
    payload = {
        "state": values.get("ActiveState", "unknown"),
        "substate": values.get("SubState", "unknown"),
        "result": values.get("Result", ""),
        "exit_code": int(values.get("ExecMainStatus", "0") or 0),
    }
    if payload["state"] == "failed" or payload["exit_code"] != 0:
        logs = subprocess.run(
            ["journalctl", "-u", UPDATE_SERVICE, "-n", "20", "--no-pager", "-o", "cat"],
            capture_output=True, text=True,
        )
        payload["error"] = logs.stdout.strip() or "Update failed"
    return payload


@app.get("/api/telemetry")
def telemetry_status():
    result = subprocess.run([str(SPAWNWP_CLI), "telemetry", "status"], capture_output=True, text=True)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"enabled": False}


@app.post("/api/telemetry/disable")
def telemetry_disable():
    result = subprocess.run([str(SPAWNWP_CLI), "telemetry", "disable"], capture_output=True, text=True)
    if result.returncode != 0:
        raise HTTPException(500, result.stderr.strip() or "Unable to disable telemetry")
    return {"enabled": False}


@app.post("/api/telemetry/enable")
def telemetry_enable():
    result = subprocess.run([str(SPAWNWP_CLI), "telemetry", "enable"], capture_output=True, text=True)
    if result.returncode != 0:
        raise HTTPException(500, result.stderr.strip() or "Unable to enable telemetry")
    status = subprocess.run([str(SPAWNWP_CLI), "telemetry", "status"], capture_output=True, text=True)
    try:
        return json.loads(status.stdout)
    except json.JSONDecodeError:
        return {"enabled": True}

@app.get("/api/projects")
def list_projects():
    result = []
    colors = group_colors()
    for proj in get_projects():
        env = {}
        env_file = proj / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if "=" in line and not line.startswith("#"):
                    k, _, v = line.partition("=")
                    env[k.strip()] = v.strip()

        containers = []
        raw = run(
            ["docker", "compose", "ps", "--format", "json"],
            proj,
        )
        for line in raw.splitlines():
            try:
                c = json.loads(line)
                containers.append({
                    "name": c.get("Service", ""),
                    "container": c.get("Name", ""),
                    "status": c.get("Status", ""),
                    "health": c.get("Health", ""),
                })
            except json.JSONDecodeError:
                pass

        # Mailpit web UI path, served on the cockpit subdomain (same origin as
        # this dashboard) under MAILPIT_WEBROOT — so a relative path is enough.
        webroot = env.get("MAILPIT_WEBROOT", "").strip("/")
        mail_url = f"/{webroot}/" if webroot else ""

        blueprint = {"id": "legacy", "name": "Legacy", "version": "-"}
        blueprint_file = proj / ".spawnwp" / "blueprint.json"
        if blueprint_file.is_file():
            try:
                stored = json.loads(blueprint_file.read_text())
                blueprint = {key: stored.get(key, "") for key in ("id", "name", "version", "source")}
            except (OSError, json.JSONDecodeError):
                blueprint = {"id": "invalid", "name": "Invalid manifest", "version": "-"}

        expires_at = None
        days_left = None
        seconds_left = None
        if env.get("SPAWNWP_EXPIRES", "").isdigit():
            import time as _time
            expires_at = int(env["SPAWNWP_EXPIRES"])
            seconds_left = max(0, int(expires_at - _time.time()))
            days_left = round(seconds_left / 86400, 1)

        group = env.get("SPAWNWP_GROUP", "")
        result.append({
            "name": proj.name,
            "url": env.get("WP_HOME", ""),
            "group": group,
            "group_color": colors.get(group, 0) if group else 0,
            "expires_at": expires_at,
            "days_left": days_left,
            "seconds_left": seconds_left,
            "php": env.get("PHP_VERSION", "?"),
            "port": env.get("WEB_PORT", "?"),
            "db_name": env.get("DB_NAME", "wordpress"),
            "db_user": env.get("DB_USER", "wpuser"),
            "mail_url": mail_url,
            "blueprint": blueprint,
            "containers": containers,
        })
    return result


class ProjectAction(BaseModel):
    project: str
    action: str          # up | down | restart | logs | snapshot | disk | bootstrap
    service: str | None = None  # if set: action on the single service

ALLOWED_ACTIONS = {"up", "down", "restart", "logs", "snapshot", "disk", "bootstrap",
                   "xdebug-on", "xdebug-off"}
# Actions allowed when acting on a single service
PER_SERVICE_ACTIONS = {"restart", "logs"}

def project_services(proj: Path) -> list[str]:
    return run(["docker", "compose", "config", "--services"], proj).split()

# Read-only actions always allowed, even under load
READONLY_ACTIONS = {"logs", "disk"}

@app.post("/api/run")
def run_action(body: ProjectAction):
    if body.action not in ALLOWED_ACTIONS:
        raise HTTPException(400, f"Action '{body.action}' not allowed")
    if body.action not in READONLY_ACTIONS:
        guard_not_busy()
    proj = resolve_project(body.project)

    service = None
    if body.service:
        if body.action not in PER_SERVICE_ACTIONS:
            raise HTTPException(400, f"Action '{body.action}' not allowed per-service")
        if body.service not in project_services(proj):
            raise HTTPException(400, f"Unknown service '{body.service}'")
        service = body.service

    if body.action == "logs":
        cmd = ["docker", "compose", "logs", "--tail=100", "--no-color"]
        if service:
            cmd.append(service)
    elif body.action == "restart" and service:
        cmd = ["docker", "compose", "restart", service]
    elif body.action == "snapshot":
        # From the cockpit a snapshot always includes uploads (DB + media)
        cmd = ["make", "-s", "snapshot", "INCLUDE_FILES=1"]
    else:
        cmd = ["make", "-s", body.action]

    return sse_response(cmd, proj)


SNAP_LABEL_MAX = 80


def _snapshot_labels_file(proj: Path) -> Path:
    return proj / "backups" / "labels.json"


def read_snapshot_labels(proj: Path) -> dict:
    """Snapshot labels, keyed by timestamp. The snapshot files themselves are
    never renamed: the timestamp is both the id and the path-traversal defence
    (SNAP_RE), so the human name lives in this sidecar instead.

    A missing or corrupt sidecar means "no labels", never an error: labels are
    cosmetic and must not be able to break the snapshot listing.
    """
    try:
        data = json.loads(_snapshot_labels_file(proj).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, str) and SNAP_RE.match(str(k))}


def write_snapshot_labels(proj: Path, labels: dict) -> None:
    """Persist the sidecar atomically, so a crash mid-write cannot leave behind
    a truncated file that read_snapshot_labels() would silently discard."""
    path = _snapshot_labels_file(proj)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(labels, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def clean_snapshot_label(raw: str) -> str:
    """Trim a user-supplied label: control characters out, one line, capped."""
    label = "".join(c for c in (raw or "") if c.isprintable()).strip()
    return label[:SNAP_LABEL_MAX]


@app.get("/api/snapshots/{project}")
def list_snapshots(project: str):
    """List of the site's snapshots: name (timestamp), label, DB size, files."""
    proj = resolve_project(project)
    db_dir = proj / "backups" / "db"
    files_dir = proj / "backups" / "files"
    labels = read_snapshot_labels(proj)
    snaps = []
    if db_dir.is_dir():
        for f in db_dir.glob("*.sql.gz"):
            name = f.name[:-len(".sql.gz")]
            if not SNAP_RE.match(name):
                continue
            try:
                st = f.stat()
            except OSError:
                continue
            files_tar = files_dir / f"{name}.tar.gz"
            has_files = files_tar.exists()
            files_kb = (files_tar.stat().st_size // 1024) if has_files else 0
            snaps.append({
                "name": name,
                "label": labels.get(name, ""),
                "db_kb": st.st_size // 1024,
                "has_files": has_files,
                "files_kb": files_kb,
                "mtime": int(st.st_mtime),
            })
    snaps.sort(key=lambda s: s["name"], reverse=True)   # most recent first
    return snaps


class SnapshotLabel(BaseModel):
    project: str
    snapshot: str
    label: str = ""

@app.post("/api/snapshots/label")
def label_snapshot(body: SnapshotLabel):
    """Name a snapshot (or clear the name with an empty label), so a restore
    point can be recognised by what it marks rather than by its timestamp."""
    if not SNAP_RE.match(body.snapshot):
        raise HTTPException(400, "Invalid snapshot name")
    proj = resolve_project(body.project)
    if not (proj / "backups" / "db" / f"{body.snapshot}.sql.gz").is_file():
        raise HTTPException(404, f"Snapshot '{body.snapshot}' not found")
    label = clean_snapshot_label(body.label)
    labels = read_snapshot_labels(proj)
    if label:
        labels[body.snapshot] = label
    else:
        labels.pop(body.snapshot, None)
    write_snapshot_labels(proj, labels)
    return {"project": proj.name, "snapshot": body.snapshot, "label": label}


class DeleteSnapshot(BaseModel):
    project: str
    snapshot: str

@app.post("/api/snapshots/delete")
def delete_snapshot(body: DeleteSnapshot):
    """Delete a snapshot: DB dump, uploads tarball, and its label. Destructive —
    it removes a restore point — so it sits in DESTRUCTIVE_PATHS behind step-up
    re-auth, like /api/restore."""
    if not SNAP_RE.match(body.snapshot):
        raise HTTPException(400, "Invalid snapshot name")
    proj = resolve_project(body.project)
    db_file = proj / "backups" / "db" / f"{body.snapshot}.sql.gz"
    if not db_file.is_file():
        raise HTTPException(404, f"Snapshot '{body.snapshot}' not found")
    files_tar = proj / "backups" / "files" / f"{body.snapshot}.tar.gz"
    try:
        db_file.unlink()
        files_tar.unlink(missing_ok=True)
    except OSError as exc:
        raise HTTPException(500, f"Could not delete snapshot: {exc}") from exc
    labels = read_snapshot_labels(proj)
    if labels.pop(body.snapshot, None) is not None:
        write_snapshot_labels(proj, labels)
    return {"project": proj.name, "snapshot": body.snapshot, "deleted": True}


class RestoreSnapshot(BaseModel):
    project: str
    snapshot: str

@app.post("/api/restore")
def restore_snapshot(body: RestoreSnapshot):
    """Restore a snapshot (DB + uploads if present). The name is validated as a
    pure timestamp to prevent path traversal; restore.sh checks it exists."""
    if not SNAP_RE.match(body.snapshot):
        raise HTTPException(400, "Invalid snapshot name")
    guard_not_busy()
    proj = resolve_project(body.project)
    snap_file = proj / "backups" / "db" / f"{body.snapshot}.sql.gz"
    if not snap_file.is_file():
        raise HTTPException(404, f"Snapshot '{body.snapshot}' not found")
    return sse_response(["make", "-s", "restore", f"SNAPSHOT={body.snapshot}"], proj)


class PhpSwitch(BaseModel):
    project: str
    version: str

@app.post("/api/php-switch")
def php_switch(body: PhpSwitch):
    if body.version not in ("7.4", "8.2", "8.3", "8.4"):
        raise HTTPException(400, "Invalid PHP version")
    guard_not_busy()
    proj = resolve_project(body.project)
    return sse_response([
        "python3", str(PHP_SWITCH_TOOL), "--project", str(proj), "--version", body.version,
    ], proj)


class WpCliCommand(BaseModel):
    command: str


def parse_wp_cli_command(command: str) -> list[str]:
    """Validate a console command line and return the argv to pass after `wp`.

    The console runs one non-interactive `wp` process per command (argv, no
    shell), so the only rejects are the subcommands that need a TTY or stdin.
    A leading `wp` token is accepted and stripped.
    """
    try:
        args = shlex.split(command)
    except ValueError as exc:
        raise HTTPException(400, f"Could not parse the command: {exc}")
    if args and args[0] == "wp":
        args = args[1:]
    if not args:
        raise HTTPException(400, "Type a WP-CLI command, for example: plugin list")
    if any(a == "--prompt" or a.startswith("--prompt=") for a in args):
        raise HTTPException(400, "--prompt is interactive; pass the values as arguments instead")
    positional = [a for a in args if not a.startswith("-")]
    if positional[:1] == ["shell"]:
        raise HTTPException(400, "wp shell is an interactive REPL and cannot run in the console")
    if positional[:2] == ["db", "cli"]:
        raise HTTPException(400, 'wp db cli is interactive; use wp db query "SELECT ..." instead')
    if positional[:2] == ["db", "query"] and len(positional) < 3:
        raise HTTPException(400, "In the console, wp db query needs the SQL as an argument")
    return args


@app.post("/api/wp-cli/{project}")
def wp_cli(project: str, body: WpCliCommand):
    """Run a single WP-CLI command inside the site's php container and stream
    its output. No TTY and no shell around it: interactive subcommands are
    rejected up front, everything else behaves exactly like WP-CLI in a script."""
    proj = resolve_project(project)
    args = parse_wp_cli_command(body.command)
    _metric_incr("wp_cli_commands")
    return sse_response(
        ["docker", "compose", "exec", "-T", "-u", "www-data", "php", "wp", *args],
        proj,
    )


@app.get("/api/blueprints")
def list_blueprints():
    return blueprint_catalog()


# ── Per-site PHP settings (the classic hosting knobs, closed whitelist) ───────
# Values become a zz-site.ini mounted into the php container; never free text.

PHP_SIZE_RE = re.compile(r"^[0-9]{1,4}[KMG]$")

PHP_INI_DEFAULTS = {
    "memory_limit": "256M",
    "upload_max_filesize": "64M",
    "post_max_size": "64M",
    "max_execution_time": 120,
    "max_input_vars": 3000,
    "max_input_time": -1,
    "display_errors": False,
}


def _size_mb(value: str) -> int:
    unit = value[-1]
    n = int(value[:-1])
    return {"K": max(1, n // 1024), "M": n, "G": n * 1024}[unit]


class PhpIniSettings(BaseModel):
    memory_limit: str = "256M"
    upload_max_filesize: str = "64M"
    post_max_size: str = "64M"
    max_execution_time: int = 120
    max_input_vars: int = 3000
    max_input_time: int = -1
    display_errors: bool = False

    def validated(self) -> "PhpIniSettings":
        for field in ("memory_limit", "upload_max_filesize", "post_max_size"):
            if not PHP_SIZE_RE.match(getattr(self, field)):
                raise HTTPException(400, f"Invalid {field}: use a number with K/M/G unit (e.g. 128M)")
        if not 16 <= _size_mb(self.memory_limit) <= 1024:
            raise HTTPException(400, "memory_limit must be between 16M and 1G")
        for field in ("upload_max_filesize", "post_max_size"):
            if not 1 <= _size_mb(getattr(self, field)) <= 512:
                raise HTTPException(400, f"{field} must be between 1M and 512M")
        if not 10 <= self.max_execution_time <= 3600:
            raise HTTPException(400, "max_execution_time must be between 10 and 3600 seconds")
        if not 100 <= self.max_input_vars <= 100000:
            raise HTTPException(400, "max_input_vars must be between 100 and 100000")
        if not -1 <= self.max_input_time <= 3600:
            raise HTTPException(400, "max_input_time must be between -1 and 3600")
        return self

    def as_env(self) -> dict:
        return {
            "SPAWNWP_PHP_MEMORY_LIMIT": self.memory_limit,
            "SPAWNWP_PHP_UPLOAD_MAX_FILESIZE": self.upload_max_filesize,
            "SPAWNWP_PHP_POST_MAX_SIZE": self.post_max_size,
            "SPAWNWP_PHP_MAX_EXECUTION_TIME": str(self.max_execution_time),
            "SPAWNWP_PHP_MAX_INPUT_VARS": str(self.max_input_vars),
            "SPAWNWP_PHP_MAX_INPUT_TIME": str(self.max_input_time),
            "SPAWNWP_PHP_DISPLAY_ERRORS": "On" if self.display_errors else "Off",
        }


class NewProject(BaseModel):
    name: str | None = None
    blueprint: str = "development"
    php_version: str | None = None
    wordpress_version: str | None = None   # override the blueprint's pinned WP version (e.g. "latest")
    php_settings: PhpIniSettings | None = None
    lifetime_days: int = 0   # 0 = permanent; otherwise the site self-destructs
    lifetime_seconds: int | None = None
    install_deploy_plugin: bool = False   # opt-in: bundle the SpawnWP Deploy plugin
    deactivate_plugins: bool = False   # captured blueprints: leave plugins inactive
    group: str = ""   # optional Manage-dashboard group label


def prepare_new_project(body: NewProject) -> tuple[str, list[str], dict | None]:
    """Validate one create request and return its stable name, command and env."""
    name = body.name or random_project_name()
    if not SLUG_RE.match(name):
        raise HTTPException(400, "Invalid name: use lowercase letters, digits and hyphens only")
    if not SLUG_RE.match(body.blueprint):
        raise HTTPException(400, "Invalid blueprint id")
    if not 0 <= body.lifetime_days <= 365:
        raise HTTPException(400, "lifetime_days must be between 0 and 365")
    if body.lifetime_seconds is not None and not 300 <= body.lifetime_seconds <= 365 * 86400:
        raise HTTPException(400, "lifetime_seconds must be between 300 and 31536000")
    validate_blueprint_choice(body.blueprint, body.php_version, body.wordpress_version)
    guard_not_busy()
    guard_capacity()
    if is_project(PROJECTS_ROOT / name):
        raise HTTPException(409, f"Project '{name}' already exists")
    group = body.group.strip()
    if group and not GROUP_RE.match(group):
        raise HTTPException(400, "Invalid group: use letters, digits, spaces, dots, hyphens "
                                 "or underscores (max 32 characters)")
    env = body.php_settings.validated().as_env() if body.php_settings else {}
    if group:
        env["SPAWNWP_GROUP"] = group
    if body.lifetime_seconds is not None:
        env["SPAWNWP_SITE_LIFETIME_SECONDS"] = str(body.lifetime_seconds)
    elif body.lifetime_days:
        env["SPAWNWP_SITE_LIFETIME_DAYS"] = str(body.lifetime_days)
    if body.install_deploy_plugin:
        env["SPAWNWP_INSTALL_DEPLOY_PLUGIN"] = "1"
    if body.deactivate_plugins:
        env["SPAWNWP_DEACTIVATE_PLUGINS"] = "1"
    command = [
        "bash", str(PRIMARY_PROJECT / "scripts" / "new-project.sh"),
        name, body.blueprint, body.php_version or "", body.wordpress_version or "",
    ]
    return name, command, env or None


CREATION_SECRET_RE = re.compile(
    r"(?i)\b(password|pass|token|secret)(\s*[:=]\s*)(\S+)",
)


def creation_failure_detail(output: str) -> str:
    """Select a useful, single-line creation error without returning secrets."""
    for raw in reversed(output.splitlines()):
        line = raw.strip()
        lower = line.lower()
        if not line or lower.startswith(("==>", "->", "!! creation failed", "rolling back")):
            continue
        if lower.startswith(("admin credentials:", "user:", "pass:", "password:")):
            continue
        return CREATION_SECRET_RE.sub(r"\1\2[redacted]", line)[:500]
    return "Site creation failed"


def run_project_creation(body: NewProject, timeout: int = 300) -> str:
    """Run site creation to completion for machine callers."""
    name, command, env = prepare_new_project(body)
    proc = subprocess.Popen(
        command,
        cwd=PRIMARY_PROJECT,
        env={**os.environ, **env} if env else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    try:
        output, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
        raise HTTPException(504, f"Site creation exceeded the {timeout}-second timeout") from exc
    if proc.returncode != 0:
        detail = creation_failure_detail(output)
        print(
            f"[provision] project={name} rc={proc.returncode} error={detail}",
            file=sys.stderr,
            flush=True,
        )
        status = 409 if "another site operation" in output.lower() else 500
        raise HTTPException(status, detail)
    return name


def cleanup_created_project(name: str) -> bool:
    """Best-effort compensating cleanup after a post-create provisioning failure."""
    proj = PROJECTS_ROOT / name
    if not is_project(proj):
        return True
    try:
        subprocess.run(
            ["docker", "compose", "down", "--remove-orphans"],
            cwd=proj, capture_output=True, timeout=120,
        )
        result = subprocess.run(
            ["bash", str(PRIMARY_PROJECT / "scripts" / "destroy-project.sh"), name, "--yes"],
            cwd=PRIMARY_PROJECT, capture_output=True, timeout=120,
        )
        return result.returncode == 0 and not proj.exists()
    except (OSError, subprocess.TimeoutExpired):
        return False


@app.post("/api/new-project")
def new_project(body: NewProject):
    _, command, env = prepare_new_project(body)
    return sse_response(command, PRIMARY_PROJECT, env)


def _running_count(proj: Path) -> int:
    out = run(["docker", "compose", "ps", "-q", "--status", "running"], proj)
    return len([l for l in out.splitlines() if l.strip()])


class DestroyProject(BaseModel):
    name: str
    confirm: str   # must match the project name (guards against accidental click)

@app.post("/api/destroy")
def destroy_project(body: DestroyProject):
    """PERMANENTLY destroy a site: containers, volumes, dir and Nginx block.
    Constraints: valid name, never the primary stack, explicit confirm, containers down."""
    if not SLUG_RE.match(body.name):
        raise HTTPException(400, "Invalid name")
    if body.name == PRIMARY_PROJECT.name:
        raise HTTPException(400, "The primary stack cannot be destroyed")
    if body.confirm != body.name:
        raise HTTPException(400, "Confirmation does not match")
    guard_not_busy()
    proj = resolve_project(body.name)
    if _running_count(proj) > 0:
        raise HTTPException(409, "Containers still running: bring them 'Down' first")
    return sse_response(
        ["bash", str(PRIMARY_PROJECT / "scripts" / "destroy-project.sh"), body.name],
        PRIMARY_PROJECT,
    )


# ── Metrics (read-only) ──────────────────────────────────────────────────────────

@app.get("/api/stats")
def stats():
    """CPU/RAM per container (a single `docker stats`, keyed by container name)."""
    raw = run(
        ["docker", "stats", "--no-stream", "--format",
         "{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}|{{.MemPerc}}"],
        PRIMARY_PROJECT,
    )
    out = {}
    for line in raw.splitlines():
        parts = line.split("|")
        if len(parts) != 4:
            continue
        name, cpu, mem, mem_pct = parts
        used, _, limit = mem.partition("/")
        out[name] = {
            "cpu": cpu.strip(),
            "mem_used": used.strip(),
            "mem_limit": limit.strip(),
            "mem_pct": mem_pct.strip(),
        }
    return out


@app.get("/api/host")
def host():
    """Host RAM, disk, load and uptime — read from /proc and shutil (no shell)."""
    mem = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        k, _, v = line.partition(":")
        if v:
            mem[k.strip()] = int(v.strip().split()[0])  # kB
    total_kb = mem.get("MemTotal", 0)
    avail_kb = mem.get("MemAvailable", 0)
    used_kb = max(total_kb - avail_kb, 0)

    du = shutil.disk_usage("/")
    disk_used = du.total - du.free

    try:
        load = [round(x, 2) for x in os.getloadavg()]
    except OSError:
        load = [0, 0, 0]
    try:
        up_seconds = float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError):
        up_seconds = 0

    return {
        "ram": {
            "used_mb": used_kb // 1024,
            "total_mb": total_kb // 1024,
            "pct": round(used_kb / total_kb * 100, 1) if total_kb else 0,
        },
        "disk": {
            "used_gb": round(disk_used / 1e9, 1),
            "total_gb": round(du.total / 1e9, 1),
            "pct": round(disk_used / du.total * 100, 1) if du.total else 0,
        },
        "load": load,
        "uptime_h": round(up_seconds / 3600, 1),
        "status": system_status(),
    }


@app.get("/api/db/{project}")
def db_info(project: str):
    """DB size (MB) and table count, in a single query via WP-CLI."""
    proj = resolve_project(project)
    sql = ("SELECT ROUND(SUM(data_length+index_length)/1024/1024,2), COUNT(*) "
           "FROM information_schema.tables WHERE table_schema=DATABASE();")
    out = run(
        ["docker", "compose", "exec", "-T", "-u", "www-data", "php",
         "wp", "db", "query", sql, "--skip-column-names"],
        proj,
    )
    size_mb, tables = None, None
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 2:
            try:
                size_mb = float(parts[0])
                tables = int(parts[1])
            except ValueError:
                pass
    return {"size_mb": size_mb, "tables": tables}


@app.get("/api/db/{project}/secret")
def db_secret(project: str):
    """Project DB password (for the 'copy' button → Adminer login).
    Behind HTTPS and the mandatory application login; whoever reaches this endpoint
    already has full control of the cockpit."""
    proj = resolve_project(project)
    env_file = proj / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("DB_PASS="):
                return {"password": line.partition("=")[2].strip()}
    raise HTTPException(404, "DB password not found")


def _read_env(proj: Path) -> dict:
    env = {}
    env_file = proj / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


@app.get("/api/wp/{project}/admin")
def wp_admin(project: str):
    """Site's WordPress admin credentials (read from .env). Behind the cockpit's
    3 security layers: whoever gets here already has full control."""
    proj = resolve_project(project)
    env = _read_env(proj)
    home = env.get("WP_HOME", "").rstrip("/")
    return {
        "user": env.get("WP_ADMIN_USER", "admin"),
        "password": env.get("WP_ADMIN_PASS", ""),
        "email": env.get("WP_ADMIN_EMAIL", ""),
        "url": (home + "/wp-admin/") if home else "",
    }


WP_USER_ROLES = {"administrator", "editor", "author", "contributor", "subscriber"}


class WpUserCreate(BaseModel):
    role: str
    username: str | None = None
    email: str | None = None


def create_wp_user(project: str, body: WpUserCreate) -> dict:
    proj = resolve_project(project)
    role = body.role.strip().lower()
    if role not in WP_USER_ROLES:
        raise HTTPException(400, f"Invalid role; choose one of: {', '.join(sorted(WP_USER_ROLES))}")
    username = (body.username or f"{proj.name}-{secrets.token_hex(3)}").strip().lower()
    if not WP_USERNAME_RE.match(username):
        raise HTTPException(
            400,
            "Invalid username: use lowercase letters, digits, dots, hyphens or underscores",
        )
    email = (body.email or f"{username}@spawnwp.invalid").strip().lower()
    if len(email) > 100 or not WP_EMAIL_RE.match(email):
        raise HTTPException(400, "Invalid email address")

    password = secrets.token_urlsafe(24)
    result = _php_exec(
        proj,
        [
            "wp", "user", "create", username, email,
            f"--role={role}", f"--user_pass={password}", "--porcelain",
        ],
    )
    if result.returncode != 0:
        raise _exec_error(result)
    user_id = result.stdout.decode(errors="replace").strip()
    if not user_id.isdigit():
        raise HTTPException(500, "WordPress created the user but returned an invalid user id")
    return {
        "project": proj.name,
        "id": int(user_id),
        "username": username,
        "email": email,
        "role": role,
        "password": password,
    }


@app.post("/api/wp/{project}/users")
def wp_user_create(project: str, body: WpUserCreate):
    """Create a least-privilege WordPress user; the password is returned once."""
    return create_wp_user(project, body)


# ── Magic login ──────────────────────────────────────────────────────────────
# One click into wp-admin instead of copying a username and a password around.
# It is an authentication bypass, so it is opt-in per site: the mu-plugin is
# written on request and removed when the feature is turned off. No file on the
# site, no way in.
AUTOLOGIN_MU_PLUGIN = "wp-content/mu-plugins/spawnwp-autologin.php"
AUTOLOGIN_SOURCE = PRIMARY_PROJECT / "mu-plugins" / "spawnwp-autologin.php"
AUTOLOGIN_TTL_SECONDS = 120
RESTRICTED_ADMIN_MU_PLUGIN = "wp-content/mu-plugins/spawnwp-restricted-admin.php"
RESTRICTED_ADMIN_SOURCE = PRIMARY_PROJECT / "mu-plugins" / "spawnwp-restricted-admin.php"


def _autologin_plugin_source() -> bytes:
    # Deliberately not under runtime/assets: install.sh copies that whole folder
    # into the cockpit's public /assets mount, and the mu-plugin has no business
    # being downloadable from there.
    try:
        return AUTOLOGIN_SOURCE.read_bytes()
    except OSError as exc:
        raise HTTPException(500, f"Auto-login plugin bundle missing: {exc}") from exc


def autologin_key(token: str) -> str:
    """Transient name for a magic-login token.

    Only the digest is ever stored, so the transient store never holds anything
    that can be replayed; naming the transient already requires the token. Must
    stay in step with spawnwp-autologin.php, which recomputes this same key.
    """
    return "spawnwp_autologin_" + hashlib.sha256(token.encode()).hexdigest()


def _autologin_installed(proj: Path) -> bool:
    probe = _php_exec(proj, ["test", "-f", jail_path(AUTOLOGIN_MU_PLUGIN)])
    return probe.returncode == 0


def install_autologin(proj: Path) -> None:
    target = jail_path(AUTOLOGIN_MU_PLUGIN)
    mk = _php_exec(proj, ["mkdir", "-p", "--", jail_path("wp-content/mu-plugins")])
    if mk.returncode != 0:
        raise _exec_error(mk)
    result = _php_exec(
        proj, ["dd", "of=" + target, "status=none"],
        input_bytes=_autologin_plugin_source(),
    )
    if result.returncode != 0:
        raise _exec_error(result)


def install_restricted_admin(proj: Path) -> None:
    """Install the immutable capability guard used by managed demo access."""
    try:
        source = RESTRICTED_ADMIN_SOURCE.read_bytes()
    except OSError as exc:
        raise HTTPException(500, f"Restricted-admin plugin bundle missing: {exc}") from exc
    mk = _php_exec(proj, ["mkdir", "-p", "--", jail_path("wp-content/mu-plugins")])
    if mk.returncode != 0:
        raise _exec_error(mk)
    result = _php_exec(
        proj,
        ["dd", "of=" + jail_path(RESTRICTED_ADMIN_MU_PLUGIN), "status=none"],
        input_bytes=source,
    )
    if result.returncode != 0:
        raise _exec_error(result)


@app.get("/api/wp/{project}/autologin")
def autologin_status(project: str):
    proj = resolve_project(project)
    return {"project": proj.name, "enabled": _autologin_installed(proj)}


class AutologinToggle(BaseModel):
    enabled: bool

@app.post("/api/wp/{project}/autologin")
def autologin_toggle(project: str, body: AutologinToggle):
    """Install or remove the auto-login mu-plugin on this site."""
    proj = resolve_project(project)
    guard_not_busy()
    target = jail_path(AUTOLOGIN_MU_PLUGIN)
    if body.enabled:
        install_autologin(proj)
        return {"project": proj.name, "enabled": True}
    else:
        result = _php_exec(proj, ["rm", "-f", "--", target])
    if result.returncode != 0:
        raise _exec_error(result)
    return {"project": proj.name, "enabled": body.enabled}


def mint_magic_login(proj: Path, user: str) -> dict:
    """Mint a single-use sign-in URL for one user on an auto-login-enabled site."""
    if not _autologin_installed(proj):
        raise HTTPException(409, "Magic login is off for this site: enable it first")

    env = _read_env(proj)
    home = env.get("WP_HOME", "").rstrip("/")
    if not home:
        raise HTTPException(409, "Site has no WP_HOME: cannot build a sign-in URL")
    if not user:
        raise HTTPException(409, "No WordPress user was supplied")

    lookup = _php_exec(proj, ["wp", "user", "get", user, "--field=ID"])
    if lookup.returncode != 0:
        raise _exec_error(lookup)
    user_id = lookup.stdout.decode(errors="replace").strip()
    if not user_id.isdigit():
        raise HTTPException(500, "Could not resolve the admin user id")

    token = secrets.token_urlsafe(32)
    stored = _php_exec(proj, ["wp", "transient", "set", autologin_key(token), user_id,
                              str(AUTOLOGIN_TTL_SECONDS)])
    if stored.returncode != 0:
        raise _exec_error(stored)

    return {
        "project": proj.name,
        "url": f"{home}/?spawnwp_autologin={token}",
        "expires_in": AUTOLOGIN_TTL_SECONDS,
    }


@app.post("/api/wp/{project}/magic-login")
def magic_login(project: str):
    """Mint a single-use sign-in URL for the site's admin user.

    The cockpit stores only sha256(token), so the secret exists solely in the
    URL handed back to the caller; the mu-plugin deletes the transient before it
    authenticates, which is what makes the link genuinely single-use.
    """
    proj = resolve_project(project)
    user = _read_env(proj).get("WP_ADMIN_USER", "")
    if not user:
        raise HTTPException(409, "Site has no admin user recorded in .env")
    return mint_magic_login(proj, user)


@app.get("/api/db/{project}/login", response_class=HTMLResponse)
def db_login(project: str):
    """Adminer auto-login bridge page: from the browser session it grabs the
    CSRF token and submits the login form with the credentials from .env.
    So one click opens Adminer already authenticated, without typing anything."""
    proj = resolve_project(project)
    env = _read_env(proj)
    cfg = json.dumps({
        "base": f"/{project}-db/",
        "user": env.get("DB_USER", "wpuser"),
        "pw": env.get("DB_PASS", ""),
        "db": env.get("DB_NAME", "wordpress"),
    })
    # Neutralise a "</script>" breakout if any credential ever contained markup:
    # these escapes are valid inside a JS string and inert in HTML parsing.
    cfg = cfg.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    html = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Opening DB…</title>
<style>body{background:#0f1117;color:#e2e8f0;font-family:system-ui;display:flex;
height:100vh;margin:0;align-items:center;justify-content:center;font-size:14px}</style>
</head><body><div id="msg">🗄 Signing in to Adminer…</div>
<script>
const C = __CFG__;
(async () => {
  try {
    const r = await fetch(C.base, { credentials: 'include', cache: 'no-store' });
    const html = await r.text();
    const m = html.match(/name=['"]token['"]\\s+value=['"]([^'"]+)['"]/);
    if (!m) { location.href = C.base; return; }   // already logged in
    const f = document.createElement('form');
    f.method = 'post'; f.action = C.base;
    const add = (n, v) => { const i = document.createElement('input');
      i.type = 'hidden'; i.name = n; i.value = v; f.appendChild(i); };
    add('auth[driver]', 'server');
    add('auth[server]', 'db');
    add('auth[username]', C.user);
    add('auth[password]', C.pw);
    add('auth[db]', C.db);
    add('auth[permanent]', '1');
    add('token', m[1]);
    document.body.appendChild(f);
    f.submit();
  } catch (e) {
    document.getElementById('msg').textContent = 'Error opening DB: ' + e.message;
  }
})();
</script></body></html>"""
    return HTMLResponse(
        html.replace("__CFG__", cfg),
        headers={"Cache-Control": "no-store"},
    )


def _parse_size(s: str) -> float:
    """Convert a size like '2.477GB' / '500MB (40%)' to GB (float)."""
    if not s:
        return 0.0
    s = s.strip().split()[0]  # drop any trailing '(40%)'
    m = re.match(r"([0-9.]+)\s*([kKMGT]?B)", s)
    if not m:
        return 0.0
    units = {"B": 1, "kB": 1e3, "KB": 1e3, "MB": 1e6, "GB": 1e9, "TB": 1e12}
    return float(m.group(1)) * units.get(m.group(2), 1) / 1e9


@app.get("/api/disk")
def disk():
    """Host space (free/used) + Docker breakdown (images/volumes/cache)."""
    du = shutil.disk_usage("/")
    used = du.total - du.free
    fs = {
        "total_gb": round(du.total / 1e9, 1),
        "used_gb": round(used / 1e9, 1),
        "free_gb": round(du.free / 1e9, 1),
        "pct": round(used / du.total * 100, 1) if du.total else 0,
    }
    docker = []
    raw = run(["docker", "system", "df", "--format", "json"], PRIMARY_PROJECT)
    for line in raw.splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        docker.append({
            "type": d.get("Type", ""),
            "size_gb": round(_parse_size(d.get("Size", "0B")), 2),
            "reclaimable_gb": round(_parse_size(d.get("Reclaimable", "0B")), 2),
        })
    return {"fs": fs, "docker": docker}


@app.get("/api/disk/{project}")
def disk_project(project: str):
    """REAL disk footprint of a single site: volumes (DB/files) + writable layer
    of each container + the wp-content bind mount. Plus host space as context."""
    proj = resolve_project(project)
    prefix = f"{proj.name}_"

    # Project volumes (db_data, wp_data) with size
    volumes = []
    raw = run(["docker", "system", "df", "-v", "--format", "json"], proj)
    try:
        for v in json.loads(raw).get("Volumes", []):
            name = v.get("Name", "")
            if name.startswith(prefix):
                volumes.append({
                    "name": name[len(prefix):],
                    "mb": round(_parse_size(v.get("Size", "0B")) * 1000, 1),
                })
    except json.JSONDecodeError:
        pass

    # Writable layer per container (the part before " (virtual ...)")
    containers = []
    raw = run(["docker", "ps", "-s", "--filter", f"name={proj.name}-",
               "--format", "{{.Names}}|{{.Size}}"], proj)
    for line in raw.splitlines():
        if "|" not in line:
            continue
        cname, _, size = line.partition("|")
        writable = size.split(" (")[0]
        # service name = cname without the project prefix and the -N suffix
        svc = cname.strip()
        if svc.startswith(f"{proj.name}-"):
            svc = svc[len(proj.name) + 1:].rsplit("-", 1)[0]
        containers.append({
            "name": svc,
            "mb": round(_parse_size(writable) * 1000, 2),
        })

    # wp-content bind mount on the host
    content_mb = 0.0
    content_dir = proj / "projects"
    if content_dir.exists():
        out = run(["du", "-sm", str(content_dir)], proj)
        try:
            content_mb = float(out.split()[0])
        except (ValueError, IndexError):
            pass

    total_mb = round(sum(v["mb"] for v in volumes)
                     + sum(c["mb"] for c in containers) + content_mb, 1)

    du = shutil.disk_usage("/")
    used = du.total - du.free
    host = {
        "total_gb": round(du.total / 1e9, 1),
        "used_gb": round(used / 1e9, 1),
        "free_gb": round(du.free / 1e9, 1),
        "pct": round(used / du.total * 100, 1) if du.total else 0,
    }

    return {
        "project": proj.name,
        "volumes": volumes,
        "containers": containers,
        "content_mb": round(content_mb, 1),
        "total_mb": total_mb,
        "host": host,
    }


# ── Site expiry: extend or remove a temporary site's lifetime ─────────────────
# Only lengthens or removes the deadline (never shortens to "now"): the actual
# destruction is done by the five-minute spawnwp-site-expiry timer via site-expiry.sh.

class SiteExpiry(BaseModel):
    lifetime_days: int = 0   # counted from now; 0 = make the site permanent
    lifetime_seconds: int | None = None


def set_project_lifetime(proj: Path, lifetime_seconds: int | None,
                         lifetime_days: int = 0) -> int | None:
    """Set a deadline from now and return its Unix timestamp."""
    import time as _time
    env_file = proj / ".env"
    lines = [line for line in env_file.read_text().splitlines()
             if not line.startswith("SPAWNWP_EXPIRES=")]
    expires_at = None
    if lifetime_seconds is not None:
        expires_at = int(_time.time()) + lifetime_seconds
        lines.append(f"SPAWNWP_EXPIRES={expires_at}")
    elif lifetime_days:
        expires_at = int(_time.time()) + lifetime_days * 86400
        lines.append(f"SPAWNWP_EXPIRES={expires_at}")
    env_file.write_text("\n".join(lines) + "\n")
    return expires_at


@app.post("/api/expiry/{project}")
def set_expiry(project: str, body: SiteExpiry):
    proj = resolve_project(project)
    if proj == PRIMARY_PROJECT:
        raise HTTPException(400, "The primary stack cannot expire")
    if not 0 <= body.lifetime_days <= 365:
        raise HTTPException(400, "lifetime_days must be between 0 and 365")
    if body.lifetime_seconds is not None and not 300 <= body.lifetime_seconds <= 365 * 86400:
        raise HTTPException(400, "lifetime_seconds must be between 300 and 31536000")
    set_project_lifetime(proj, body.lifetime_seconds, body.lifetime_days)
    return {
        "project": proj.name,
        "lifetime_days": body.lifetime_days,
        "lifetime_seconds": body.lifetime_seconds,
    }


# ── Site group: a free-text label used to group sites on the Manage dashboard ──
# Stored per site in its .env; purely cosmetic (nothing in the stack reads it but
# the cockpit). The charset is restricted so the label can never break .env
# parsing or Compose variable substitution.

class SiteGroup(BaseModel):
    group: str   # "" clears the group (site becomes ungrouped)


@app.post("/api/group/{project}")
def set_group(project: str, body: SiteGroup):
    proj = resolve_project(project)
    group = body.group.strip()
    if group and not GROUP_RE.match(group):
        raise HTTPException(400, "Invalid group: use letters, digits, spaces, dots, hyphens "
                                 "or underscores (max 32 characters)")
    env_file = proj / ".env"
    lines = [l for l in env_file.read_text().splitlines() if not l.startswith("SPAWNWP_GROUP=")]
    if group:
        lines.append(f"SPAWNWP_GROUP={group}")
    env_file.write_text("\n".join(lines) + "\n")
    return {"project": proj.name, "group": group}


class GroupColor(BaseModel):
    group: str
    color: int   # 1..6 = palette entry; 0 clears the colour


@app.post("/api/group-colors")
def set_group_color(body: GroupColor):
    """Colour a group (not a site): purely cosmetic, shared across browsers."""
    group = body.group.strip()
    if not GROUP_RE.match(group):
        raise HTTPException(400, "Invalid group")
    if not 0 <= body.color <= GROUP_COLOR_MAX:
        raise HTTPException(400, f"color must be between 0 and {GROUP_COLOR_MAX}")

    colors = group_colors()
    if body.color:
        colors[group] = body.color
    else:
        colors.pop(group, None)

    # Garbage-collect labels no site uses any more (renamed or destroyed sites).
    in_use = set()
    for proj in get_projects():
        env_file = proj / ".env"
        if not env_file.exists():
            continue
        for line in env_file.read_text().splitlines():
            if line.startswith("SPAWNWP_GROUP="):
                in_use.add(line.partition("=")[2].strip())
    colors = {label: color for label, color in colors.items() if label in in_use}

    GROUP_COLORS_FILE.parent.mkdir(parents=True, exist_ok=True)
    GROUP_COLORS_FILE.write_text(json.dumps(colors, indent=2, sort_keys=True) + "\n")
    return {"group": group, "color": colors.get(group, 0)}


# ── Per-site PHP settings: read / apply on an existing site ───────────────────

PHP_INI_APPLY_TOOL = PRIMARY_PROJECT / "scripts" / "php-ini-apply.sh"


@app.get("/api/php-ini/{project}")
def get_php_ini(project: str):
    proj = resolve_project(project)
    supported = "zz-site.ini" in (proj / "compose.yaml").read_text()
    values = dict(PHP_INI_DEFAULTS)
    ini = proj / "docker" / "php" / "zz-site.ini"
    if ini.is_file():
        for line in ini.read_text().splitlines():
            if "=" not in line or line.lstrip().startswith(";"):
                continue
            key, _, raw = line.partition("=")
            key, raw = key.strip(), raw.strip()
            if key not in values:
                continue
            if key == "display_errors":
                values[key] = raw == "On"
            elif isinstance(PHP_INI_DEFAULTS[key], int):
                try:
                    values[key] = int(raw)
                except ValueError:
                    pass
            else:
                values[key] = raw
    return {"project": proj.name, "supported": supported, "settings": values}


@app.post("/api/php-ini/{project}")
def set_php_ini(project: str, body: PhpIniSettings):
    proj = resolve_project(project)
    if proj == PRIMARY_PROJECT:
        raise HTTPException(400, "The primary stack's PHP settings are not managed here")
    if "zz-site.ini" not in (proj / "compose.yaml").read_text():
        raise HTTPException(409, "This site was created before SpawnWP 0.3.14 and has no "
                                 "per-site PHP overrides mount. Recreate it to use PHP settings.")
    guard_not_busy()
    env = body.validated().as_env()
    result = subprocess.run(
        ["bash", str(PHP_INI_APPLY_TOOL), proj.name],
        capture_output=True, text=True, cwd=PRIMARY_PROJECT,
        env={**os.environ, **env}, timeout=120,
    )
    output = (result.stdout + result.stderr).strip()
    if result.returncode != 0:
        raise HTTPException(500, output.splitlines()[-1] if output else "Failed to apply PHP settings")
    return {"project": proj.name, "settings": body.model_dump(), "output": output}


# ── Per-site file manager (jailed inside the site's php container) ────────────
# Every operation runs `docker compose exec -T -u www-data php <op>` rooted at
# /var/www/html. The container boundary IS the jail: a path-traversal bug cannot
# reach the host or another site, and running as www-data (uid 33) keeps files
# owned the way php-fpm needs. Reads are open; writes/uploads/deletes go through
# the middleware's recent-auth step-up (see requires_recent_auth / FILE_WRITE_RE).

DOCROOT = "/var/www/html"
FILE_VIEW_CAP = 1024 * 1024        # inline text view / editor cap: 1 MiB
FILE_UPLOAD_CAP = 2 * 1024 ** 3    # single-file upload cap: 2 GiB


def jail_path(rel: str) -> str:
    """Resolve a client path relative to the container docroot.

    Rejects absolute paths, NUL/newline, and anything that escapes the docroot
    after normalisation. Returns an absolute in-container path under DOCROOT.
    """
    rel = rel or ""
    if any(c in rel for c in ("\x00", "\n", "\r")):
        raise HTTPException(400, "Invalid path")
    if rel.startswith("/"):
        raise HTTPException(400, "Path must be relative to the site root")
    normalized = posixpath.normpath(rel)
    if normalized in (".", ""):
        return DOCROOT
    if normalized == ".." or normalized.startswith("../"):
        raise HTTPException(400, "Path escapes the site root")
    return f"{DOCROOT}/{normalized}"


def _rel(path: str) -> str:
    """The normalised relative path echoed back to the client ('' = root)."""
    norm = posixpath.normpath(path or "")
    return "" if norm in (".", "") else norm


def _php_exec(proj: Path, argv: list[str], input_bytes: bytes | None = None):
    """Run a command inside the site's php container as www-data (argv, no shell)."""
    return subprocess.run(
        ["docker", "compose", "exec", "-T", "-u", "www-data", "php", *argv],
        cwd=proj, input=input_bytes, capture_output=True, timeout=120,
    )


def _exec_error(result) -> HTTPException:
    err = (result.stderr.decode(errors="replace") if isinstance(result.stderr, bytes)
           else (result.stderr or "")).strip()
    low = err.lower()
    if "not running" in low or "no container" in low:
        return HTTPException(409, "The site is down: bring it Up to browse its files")
    if "no such file" in low or "cannot stat" in low or "not found" in low:
        return HTTPException(404, "Path not found")
    return HTTPException(400, err.splitlines()[-1] if err else "File operation failed")


class FilePath(BaseModel):
    path: str


class FileWrite(BaseModel):
    path: str
    content: str


class FileRename(BaseModel):
    path: str
    to: str


@app.get("/api/files/{project}")
def files_list(project: str, path: str = ""):
    """List one directory level inside the site's docroot."""
    proj = resolve_project(project)
    target = jail_path(path)
    result = _php_exec(proj, [
        "find", target, "-maxdepth", "1", "-mindepth", "1",
        "-printf", "%y\\t%s\\t%T@\\t%m\\t%f\\n",
    ])
    if result.returncode != 0:
        raise _exec_error(result)
    entries = []
    for line in result.stdout.decode(errors="replace").splitlines():
        parts = line.split("\t")
        if len(parts) != 5:
            continue
        typ, size, mtime, mode, name = parts
        entries.append({
            "name": name,
            "type": "dir" if typ == "d" else "link" if typ == "l" else "file",
            "size": int(size) if size.isdigit() else 0,
            "mtime": float(mtime) if mtime.replace(".", "", 1).isdigit() else 0,
            "mode": mode,
        })
    entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))
    return {"project": proj.name, "path": _rel(path), "entries": entries}


def _stat_kind_size(proj: Path, target: str) -> tuple[str, int]:
    info = _php_exec(proj, ["stat", "-c", "%F\t%s", target])
    if info.returncode != 0:
        raise _exec_error(info)
    kind, _, size = info.stdout.decode(errors="replace").strip().partition("\t")
    return kind, int(size) if size.isdigit() else 0


@app.get("/api/files/{project}/read")
def files_read(project: str, path: str):
    """Return a text file's content for inline viewing/editing (capped)."""
    proj = resolve_project(project)
    if _rel(path) == "":
        raise HTTPException(400, "Not a file")
    target = jail_path(path)
    kind, size = _stat_kind_size(proj, target)
    if "directory" in kind:
        raise HTTPException(400, "Path is a directory")
    if size > FILE_VIEW_CAP:
        raise HTTPException(413, "File too large to view inline — download it instead")
    data = _php_exec(proj, ["base64", target])
    if data.returncode != 0:
        raise _exec_error(data)
    raw = base64.b64decode(data.stdout)
    try:
        return {"project": proj.name, "path": _rel(path), "binary": False,
                "size": len(raw), "content": raw.decode("utf-8")}
    except UnicodeDecodeError:
        return {"project": proj.name, "path": _rel(path), "binary": True,
                "size": len(raw), "content": ""}


@app.get("/api/files/{project}/download")
def files_download(project: str, path: str):
    """Stream a file out as an attachment (raw bytes, any size)."""
    proj = resolve_project(project)
    if _rel(path) == "":
        raise HTTPException(400, "Not a file")
    target = jail_path(path)
    kind, _ = _stat_kind_size(proj, target)
    if "directory" in kind:
        raise HTTPException(400, "Path is a directory")

    def stream():
        proc = subprocess.Popen(
            ["docker", "compose", "exec", "-T", "-u", "www-data", "php", "cat", target],
            cwd=proj, stdout=subprocess.PIPE,
        )
        try:
            while True:
                chunk = proc.stdout.read(65536)
                if not chunk:
                    break
                yield chunk
        finally:
            proc.stdout.close()
            proc.wait()

    safe = posixpath.basename(target).replace('"', "").replace("\\", "") or "download"
    return StreamingResponse(
        stream(), media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{safe}"'},
    )


@app.post("/api/files/{project}/write")
def files_write(project: str, body: FileWrite):
    proj = resolve_project(project)
    if _rel(body.path) == "":
        raise HTTPException(400, "Provide a file path")
    target = jail_path(body.path)
    guard_not_busy()
    data = body.content.encode("utf-8")
    if len(data) > FILE_VIEW_CAP:
        raise HTTPException(413, "Content too large for the editor")
    result = _php_exec(proj, ["dd", "of=" + target, "status=none"], input_bytes=data)
    if result.returncode != 0:
        raise _exec_error(result)
    _metric_incr("file_ops")
    return {"project": proj.name, "path": _rel(body.path), "bytes": len(data)}


@app.post("/api/files/{project}/upload")
async def files_upload(project: str, request: Request, path: str = "", filename: str = ""):
    # The file rides in the raw request body (no multipart, so no python-multipart
    # dependency); the destination folder and name come as query parameters.
    proj = resolve_project(project)
    name = posixpath.basename(filename or "").strip()
    if not name or name in (".", ".."):
        raise HTTPException(400, "Invalid upload filename")
    target = jail_path(posixpath.join(_rel(path), name))
    guard_not_busy()
    proc = subprocess.Popen(
        ["docker", "compose", "exec", "-T", "-u", "www-data", "php",
         "dd", "of=" + target, "status=none"],
        cwd=proj, stdin=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    total = 0
    try:
        async for chunk in request.stream():
            if not chunk:
                continue
            total += len(chunk)
            if total > FILE_UPLOAD_CAP:
                proc.kill()
                raise HTTPException(413, "Upload exceeds the 2 GiB per-file limit")
            proc.stdin.write(chunk)
    finally:
        if proc.stdin and not proc.stdin.closed:
            proc.stdin.close()
    stderr = proc.stderr.read() if proc.stderr else b""
    if proc.wait() != 0:
        raise _exec_error(subprocess.CompletedProcess(proc.args, proc.returncode, b"", stderr))
    _metric_incr("file_ops")
    return {"project": proj.name, "path": _rel(posixpath.join(_rel(path), name)), "bytes": total}


@app.post("/api/files/{project}/mkdir")
def files_mkdir(project: str, body: FilePath):
    proj = resolve_project(project)
    if _rel(body.path) == "":
        raise HTTPException(400, "Provide a folder path")
    target = jail_path(body.path)
    guard_not_busy()
    result = _php_exec(proj, ["mkdir", "-p", "--", target])
    if result.returncode != 0:
        raise _exec_error(result)
    _metric_incr("file_ops")
    return {"project": proj.name, "path": _rel(body.path)}


UNZIP_MAX_TOTAL = 2 * 1024 ** 3     # refuse archives that expand past 2 GiB
UNZIP_MAX_ENTRIES = 20000


def unsafe_zip_entries(listing: str) -> list[str]:
    """Entry names in `unzip -l` output that would escape the extraction folder.

    `unzip` strips leading slashes and refuses "../" by default, but that is a
    behaviour of the tool, not a guarantee we control: jail_path() validates the
    path of the *archive*, and says nothing about its *contents*. So the archive
    is inspected first and rejected outright — a zip-slip must never depend on a
    single external binary continuing to behave.
    """
    unsafe = []
    for line in listing.splitlines():
        # Format: "  <size>  <date> <time>   <name>" — the name is the 4th field
        # and may itself contain spaces, so split at most 3 times.
        parts = line.split(None, 3)
        if len(parts) < 4 or not parts[0].isdigit():
            continue                      # header, separator or summary line
        name = parts[3].strip()
        if not name:
            continue
        if name.startswith("/") or name.startswith("\\") or ".." in Path(name).parts:
            unsafe.append(name)
        elif "\x00" in name or "\n" in name:
            unsafe.append(name)
    return unsafe


def zip_uncompressed_size(listing: str) -> tuple[int, int]:
    """(total uncompressed bytes, entry count) from `unzip -l` output."""
    total = entries = 0
    for line in listing.splitlines():
        parts = line.split(None, 3)
        if len(parts) < 4 or not parts[0].isdigit():
            continue
        total += int(parts[0])
        entries += 1
    return total, entries


@app.post("/api/files/{project}/unzip")
def files_unzip(project: str, body: FilePath):
    """Extract a .zip in place, into the folder that contains it.

    Uploading a zip and expanding it is the only practical way to get a theme or
    a plugin tree into a site through the file manager, which otherwise takes one
    file at a time.
    """
    proj = resolve_project(project)
    rel = _rel(body.path)
    if not rel:
        raise HTTPException(400, "Provide the path of a .zip file")
    if not rel.lower().endswith(".zip"):
        raise HTTPException(400, "Only .zip archives can be extracted")
    target = jail_path(rel)
    dest = jail_path(posixpath.dirname(rel))
    guard_not_busy()

    listing = _php_exec(proj, ["unzip", "-l", "--", target])
    if listing.returncode != 0:
        raise _exec_error(listing)
    text = listing.stdout.decode(errors="replace")

    unsafe = unsafe_zip_entries(text)
    if unsafe:
        raise HTTPException(
            400,
            "Refusing to extract: the archive contains entries that would escape "
            f"its folder (e.g. {unsafe[0]!r})",
        )
    total, entries = zip_uncompressed_size(text)
    if entries > UNZIP_MAX_ENTRIES:
        raise HTTPException(413, f"Archive has too many entries ({entries})")
    if total > UNZIP_MAX_TOTAL:
        raise HTTPException(413, "Archive would expand past the 2 GiB limit")

    result = _php_exec(proj, ["unzip", "-o", "-q", "--", target, "-d", dest])
    if result.returncode != 0:
        raise _exec_error(result)
    _metric_incr("file_ops")
    return {"project": proj.name, "path": rel, "entries": entries, "bytes": total}


@app.post("/api/files/{project}/rename")
def files_rename(project: str, body: FileRename):
    proj = resolve_project(project)
    if _rel(body.path) == "" or _rel(body.to) == "":
        raise HTTPException(400, "Refusing to move the site root")
    src, dst = jail_path(body.path), jail_path(body.to)
    guard_not_busy()
    result = _php_exec(proj, ["mv", "--", src, dst])
    if result.returncode != 0:
        raise _exec_error(result)
    _metric_incr("file_ops")
    return {"project": proj.name, "path": _rel(body.path), "to": _rel(body.to)}


@app.post("/api/files/{project}/delete")
def files_delete(project: str, body: FilePath):
    proj = resolve_project(project)
    if _rel(body.path) == "":
        raise HTTPException(400, "Refusing to delete the site root")
    target = jail_path(body.path)
    guard_not_busy()
    result = _php_exec(proj, ["rm", "-rf", "--", target])
    if result.returncode != 0:
        raise _exec_error(result)
    _metric_incr("file_ops")
    return {"project": proj.name, "path": _rel(body.path)}


# ── System info: PHP image inventory + manual lifecycle ───────────────────────
# Images are shared across sites (one per PHP version, ~1.8 GB each). Keeping
# them makes every deploy fast (~35s); deleting one frees the space but the next
# deploy on that PHP version rebuilds it (~5 min). Nothing rebuilds or deletes
# automatically unless the admin opts into the auto-delete setting below.

METRICS_FILE = Path(os.environ.get("SPAWNWP_METRICS_FILE", "/var/lib/spawnwp/metrics.json"))


def _metric_incr(key: str, n: int = 1) -> None:
    """Best-effort bump of a local aggregate counter (see scripts/lib-metrics.sh)."""
    import fcntl
    try:
        METRICS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(f"{METRICS_FILE}.lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                data = json.loads(METRICS_FILE.read_text())
                if not isinstance(data, dict):
                    data = {}
            except (OSError, ValueError):
                data = {}
            data[key] = int(data.get(key, 0)) + n
            tmp = Path(f"{METRICS_FILE}.tmp")
            tmp.write_text(json.dumps(data, sort_keys=True))
            tmp.replace(METRICS_FILE)
    except Exception:
        pass
PHP_IMAGE_REPO = "wp-dev-php"
REFRESH_IMAGE_TOOL = PRIMARY_PROJECT / "scripts" / "refresh-image.sh"
PHP_VER_RE = re.compile(r"^[0-9]+\.[0-9]+$")
# Image tags carry the WordPress version when a site pins one: "8.4", "8.4-wp7.0.1".
# The image content depends on it (the Dockerfile bakes WORDPRESS_VERSION in), so
# the tag must too — otherwise pinned and latest sites re-tag one shared name from
# under each other and rebuild forever. See runtime/scripts/lib-image.sh.
IMAGE_TAG_RE = re.compile(r"^([0-9]+\.[0-9]+)(?:-wp([0-9.]+))?$")


def _config_env_get(key: str, default: str) -> str:
    if CONFIG_ENV.is_file():
        for line in CONFIG_ENV.read_text().splitlines():
            if line.startswith(f"{key}="):
                return line.partition("=")[2].strip()
    return default


def _config_env_set(key: str, value: str) -> None:
    lines = CONFIG_ENV.read_text().splitlines() if CONFIG_ENV.is_file() else []
    replaced = False
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{key}={value}")
    CONFIG_ENV.write_text("\n".join(lines) + "\n")


def _image_age_days(created: str) -> int:
    from datetime import datetime, timezone
    # Docker returns RFC3339 with nanoseconds; trim to microseconds for fromisoformat.
    iso = re.sub(r"\.(\d{6})\d*", r".\1", created.replace("Z", "+00:00"))
    try:
        then = datetime.fromisoformat(iso)
    except ValueError:
        return 0
    return max(0, int((datetime.now(timezone.utc) - then).total_seconds() // 86400))


def _image_tags_in_use() -> dict[str, list[str]]:
    """Map image tag ("8.4", "8.4-wp7.0.1") -> the sites running it.

    Resolved exactly the way compose resolves it — PHP_VERSION + WP_IMAGE_SUFFIX
    straight from the site's .env — and deliberately NOT re-derived from
    WP_VERSION: sites created before 0.5.20 have no WP_IMAGE_SUFFIX and really do
    run the unsuffixed tag, whatever WordPress they pinned. This map is what stops
    the image GC and the delete button removing an image out from under a running
    site, so it has to describe reality rather than intent.
    """
    used: dict[str, list[str]] = {}
    for proj in get_projects():
        env = _read_env(proj)
        ver = env.get("PHP_VERSION", "")
        if ver:
            used.setdefault(ver + env.get("WP_IMAGE_SUFFIX", ""), []).append(proj.name)
    return used


@app.get("/api/images")
def list_images():
    stale_days = int(_config_env_get("SPAWNWP_IMAGE_MAX_AGE_DAYS", "7") or 7)
    used = _image_tags_in_use()
    images = []
    raw = run(["docker", "image", "ls", PHP_IMAGE_REPO, "--format", "json"], PRIMARY_PROJECT)
    for line in raw.splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        tag = entry.get("Tag", "")
        match = IMAGE_TAG_RE.match(tag)
        if not match:
            continue
        created = run(["docker", "image", "inspect", "-f", "{{.Created}}",
                       f"{PHP_IMAGE_REPO}:{tag}"], PRIMARY_PROJECT)
        age = _image_age_days(created)
        images.append({
            "tag": f"{PHP_IMAGE_REPO}:{tag}",
            "image_tag": tag,                     # what delete/refresh key on
            "php_version": match.group(1),
            "wp_version": match.group(2) or "latest",
            "size_gb": round(_parse_size(entry.get("Size", "0B")), 2),
            "age_days": age,
            "stale": age >= stale_days,
            "used_by": sorted(used.get(tag, [])),
        })
    images.sort(key=lambda i: (i["php_version"], i["wp_version"]))
    return {"images": images, "stale_days": stale_days}


class ImageDelete(BaseModel):
    image_tag: str
    confirm: str   # must match image_tag (guards against accidental click)


@app.post("/api/images/delete")
def delete_image(body: ImageDelete):
    if not IMAGE_TAG_RE.match(body.image_tag):
        raise HTTPException(400, "Invalid image tag")
    if body.confirm != body.image_tag:
        raise HTTPException(400, "Confirmation does not match")
    guard_not_busy()
    users = _image_tags_in_use().get(body.image_tag, [])
    if users:
        raise HTTPException(409, f"Image in use by: {', '.join(sorted(users))}. "
                                 "Destroy or switch those sites first.")
    tag = f"{PHP_IMAGE_REPO}:{body.image_tag}"
    out = run(["docker", "rmi", tag], PRIMARY_PROJECT)
    if "Error" in out or "unable" in out.lower():
        raise HTTPException(409, out.splitlines()[-1] if out else "Unable to delete the image")
    _metric_incr("image_deletes")
    return {"deleted": tag}


class ImageRefresh(BaseModel):
    php_version: str


@app.post("/api/images/refresh")
def refresh_image(body: ImageRefresh):
    if not PHP_VER_RE.match(body.php_version):
        raise HTTPException(400, "Invalid PHP version")
    guard_not_busy()
    return sse_response(["bash", str(REFRESH_IMAGE_TOOL), body.php_version], PRIMARY_PROJECT)


class ImageSettings(BaseModel):
    autodelete_days: int


@app.get("/api/images/settings")
def image_settings():
    raw = _config_env_get("SPAWNWP_IMAGE_AUTODELETE_DAYS", "0")
    try:
        days = max(0, int(raw))
    except ValueError:
        days = 0
    return {"autodelete_days": days}


@app.post("/api/images/settings")
def set_image_settings(body: ImageSettings):
    if not 0 <= body.autodelete_days <= 365:
        raise HTTPException(400, "autodelete_days must be between 0 and 365")
    _config_env_set("SPAWNWP_IMAGE_AUTODELETE_DAYS", str(body.autodelete_days))
    return {"autodelete_days": body.autodelete_days}


# Imported after the site-service functions it consumes, avoiding duplicate
# validation and keeping the cockpit's existing SSE contract independent.
from provision import router as provision_router
app.include_router(provision_router)


# ── Cockpit pages and shared assets ───────────────────────────────────────────

STATIC_DIR = Path(os.environ.get("SPAWNWP_STATIC_DIR", "/srv/wp-cockpit/static"))
MODULES_ROOT = Path(os.environ.get("SPAWNWP_MODULES_ROOT", "/opt/spawnwp/modules"))
MODULE_STATE_ROOT = Path(os.environ.get("SPAWNWP_MODULE_STATE_ROOT", "/var/lib/spawnwp/modules"))
MODULE_OPERATIONS_ROOT = MODULE_STATE_ROOT / "operations"
MODULE_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,40}$")


def _module_state(module_id: str) -> dict:
    try:
        value = json.loads((MODULE_STATE_ROOT / module_id / "install.json").read_text())
    except (OSError, json.JSONDecodeError):
        value = {}
    return value if isinstance(value, dict) else {}


def _module_capabilities(release: Path) -> dict[str, bool]:
    return {
        "activate": (release / "activate.py").is_file(),
        "deactivate": (release / "deactivate.py").is_file(),
        "update": True,
        "uninstall": True,
    }


def _module_pending_operation(module_id: str) -> str:
    for path in MODULE_OPERATIONS_ROOT.glob("*.json"):
        try:
            operation = _refresh_module_operation(json.loads(path.read_text()))
        except (OSError, json.JSONDecodeError):
            continue
        if operation.get("module_id") == module_id and operation.get("state") in {"queued", "running"}:
            return str(operation.get("operation_id", operation.get("id", "")))
    return ""


def _write_module_operation(operation: dict) -> None:
    MODULE_OPERATIONS_ROOT.mkdir(parents=True, exist_ok=True)
    path = MODULE_OPERATIONS_ROOT / f"{operation['id']}.json"
    temporary = path.with_name(f".{path.name}.new")
    temporary.write_text(json.dumps(operation, indent=2, sort_keys=True) + "\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def _read_module_operation(operation_id: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", operation_id):
        raise HTTPException(404, "Operation not found")
    try:
        return json.loads((MODULE_OPERATIONS_ROOT / f"{operation_id}.json").read_text())
    except (OSError, json.JSONDecodeError):
        raise HTTPException(404, "Operation not found")


def _refresh_module_operation(operation: dict) -> dict:
    if operation.get("state") not in {"queued", "running"}:
        return operation
    pid = int(operation.get("pid", 0) or 0)
    if not pid:
        return operation
    try:
        waited, status = os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        waited, status = (0, 0) if Path(f"/proc/{pid}").exists() else (pid, 1)
    if waited == 0:
        return operation
    code = os.waitstatus_to_exitcode(status)
    output_path = Path(str(operation.get("output", "")))
    output = output_path.read_text(errors="replace")[-8000:] if output_path.is_file() else ""
    operation["state"] = "succeeded" if code == 0 else "failed"
    operation["exit_code"] = code
    operation["finished_at"] = int(__import__("time").time())
    operation["message"] = output.splitlines()[-1][:500] if output.splitlines() else operation["state"].title()
    if code != 0:
        operation["error"] = output[-1000:] or "Module operation failed"
    cleanup = operation.get("cleanup")
    if cleanup:
        shutil.rmtree(str(cleanup), ignore_errors=True)
    _write_module_operation(operation)
    return operation


def _start_module_operation(action: str, module_id: str | None, command: list[str], *, workdir: Path | None = None, cleanup: Path | None = None) -> dict:
    if not SPAWNWP_CLI.is_file():
        raise HTTPException(503, "Module manager is not installed")
    MODULE_OPERATIONS_ROOT.mkdir(parents=True, exist_ok=True)
    for path in MODULE_OPERATIONS_ROOT.glob("*.json"):
        try:
            pending = _refresh_module_operation(json.loads(path.read_text()))
        except (OSError, json.JSONDecodeError):
            continue
        if pending.get("state") in {"queued", "running"} and module_id and pending.get("module_id") == module_id:
            raise HTTPException(409, f"A module operation is already running for '{module_id}'")
        if pending.get("state") in {"queued", "running"} and action == "install" and pending.get("action") == "install":
            raise HTTPException(409, "A module installation is already running")
    operation_id = secrets.token_urlsafe(18).replace("-", "_")
    output = MODULE_OPERATIONS_ROOT / f"{operation_id}.log"
    record = {
        "id": operation_id, "operation_id": operation_id, "action": action, "module_id": module_id or "",
        "state": "queued", "created_at": int(__import__("time").time()),
        "output": str(output), "message": "Queued",
    }
    if cleanup:
        record["cleanup"] = str(cleanup)
    with output.open("w") as stream:
        try:
            process = subprocess.Popen(
                command, cwd=str(workdir) if workdir else None,
                stdout=stream, stderr=subprocess.STDOUT, start_new_session=True,
            )
        except OSError as exc:
            raise HTTPException(503, f"Unable to start module operation: {exc}") from exc
    record.update(state="running", pid=process.pid, started_at=int(__import__("time").time()), message="Running")
    _write_module_operation(record)
    return record


def installed_modules() -> list[dict]:
    """Read display-only metadata from module releases verified at installation."""
    modules = []
    for root in sorted(MODULES_ROOT.glob("*")):
        manifest = root / "current" / "module.json"
        try:
            item = json.loads(manifest.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        module_id = str(item.get("id", ""))
        admin_path = str(item.get("admin_path", ""))
        if not re.fullmatch(r"[a-z][a-z0-9-]{1,40}", module_id):
            continue
        if admin_path and not admin_path.startswith(f"/modules/{module_id}/"):
            continue
        state = _module_state(module_id)
        release = root / "current"
        recorded_source = str(state.get("source", ""))
        modules.append({
            "id": module_id,
            "name": str(item.get("name", module_id))[:80],
            "version": str(item.get("version", "unknown"))[:32],
            "description": str(item.get("description", ""))[:240],
            "admin_path": admin_path,
            "core_api_scope": str(item.get("core_api_scope", ""))[:32],
            "status": str(state.get("status", "active")),
            "last_error": str(state.get("last_error", ""))[:500],
            "updated_at": state.get("updated_at"),
            "source_url": recorded_source if recorded_source.startswith("https://") else "",
            "operation_id": _module_pending_operation(module_id),
            "capabilities": _module_capabilities(release),
        })
    return modules


@app.get("/api/modules")
def modules_api():
    return {"modules": installed_modules()}


@app.get("/api/modules/catalog")
def modules_catalog_api():
    try:
        catalog = load_module_catalog(spawnwp_version())
    except CatalogError as exc:
        raise HTTPException(503, str(exc)) from exc
    installed = installed_modules()
    return {
        "catalog_version": catalog["catalog_version"],
        "publisher": catalog["publisher"],
        "modules": catalog["modules"],
        "installed": installed,
    }


class CatalogInstallRequest(BaseModel):
    id: str
    version: str | None = None


@app.post("/api/modules/catalog/install", status_code=202)
def modules_catalog_install(body: CatalogInstallRequest):
    try:
        catalog = load_module_catalog(spawnwp_version())
    except CatalogError as exc:
        raise HTTPException(503, str(exc)) from exc
    item = next((entry for entry in catalog["modules"] if entry["id"] == body.id), None)
    if item is None:
        raise HTTPException(404, "Module is not available in the signed catalog")
    if body.version and body.version != item["version"]:
        raise HTTPException(409, "Requested module version is not the catalog version")
    return _start_module_operation(
        "install", item["id"],
        [str(SPAWNWP_CLI), "module", "install", item["archive_url"]],
    )


@app.post("/api/modules/install", status_code=202)
async def modules_install(request: Request):
    """Accept a signed package as multipart fields: archive, manifest, signature."""
    try:
        form = await request.form()
    except AssertionError as exc:
        raise HTTPException(503, "Multipart upload support is not installed") from exc
    operation_id = secrets.token_urlsafe(18).replace("-", "_")
    upload_dir = MODULE_OPERATIONS_ROOT / operation_id
    upload_dir.mkdir(parents=True, exist_ok=False)
    files = {name: form.get(name) for name in ("archive", "manifest", "signature")}
    if any(value is None or not hasattr(value, "read") for value in files.values()):
        shutil.rmtree(upload_dir, ignore_errors=True)
        raise HTTPException(400, "Upload archive, manifest and signature files together")
    limits = {"archive": 512 * 1024 * 1024, "manifest": 1024 * 1024, "signature": 1024 * 1024}
    uploaded_paths = {}
    for name, upload in files.items():
        filename = Path(str(getattr(upload, "filename", ""))).name
        if name == "archive" and not filename.endswith(".tar.gz"):
            shutil.rmtree(upload_dir, ignore_errors=True)
            raise HTTPException(400, "Archive must be a .tar.gz file")
        target = upload_dir / (filename or name)
        uploaded_paths[name] = target
        total = 0
        with target.open("wb") as stream:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > limits[name]:
                    shutil.rmtree(upload_dir, ignore_errors=True)
                    raise HTTPException(413, f"{name.title()} exceeds the upload limit")
                stream.write(chunk)
    archive = next(upload_dir.glob("*.tar.gz"), None)
    if archive is None:
        shutil.rmtree(upload_dir, ignore_errors=True)
        raise HTTPException(400, "Archive filename is invalid")
    stem = archive.name[:-7]
    manifest = upload_dir / f"{stem}.manifest.json"
    signature = upload_dir / f"{stem}.manifest.sig"
    if uploaded_paths["manifest"] != manifest:
        shutil.move(uploaded_paths["manifest"], manifest)
    if uploaded_paths["signature"] != signature:
        shutil.move(uploaded_paths["signature"], signature)
    return _start_module_operation("install", None, [str(SPAWNWP_CLI), "module", "install", str(archive)], cleanup=upload_dir)


def _validate_module_id(module_id: str) -> str:
    if not MODULE_ID_RE.fullmatch(module_id):
        raise HTTPException(400, "Invalid module id")
    return module_id


@app.post("/api/modules/{module_id}/enable", status_code=202)
def modules_enable(module_id: str):
    module_id = _validate_module_id(module_id)
    return _start_module_operation("enable", module_id, [str(SPAWNWP_CLI), "module", "enable", module_id])


@app.post("/api/modules/{module_id}/disable", status_code=202)
def modules_disable(module_id: str):
    module_id = _validate_module_id(module_id)
    return _start_module_operation("disable", module_id, [str(SPAWNWP_CLI), "module", "disable", module_id])


@app.post("/api/modules/{module_id}/update", status_code=202)
def modules_update(module_id: str, source: str | None = Query(default=None)):
    module_id = _validate_module_id(module_id)
    command = [str(SPAWNWP_CLI), "module", "update", module_id]
    if source:
        if not source.startswith("https://"):
            raise HTTPException(400, "Update source must use HTTPS")
        command.extend(["--source", source])
    return _start_module_operation("update", module_id, command)


@app.delete("/api/modules/{module_id}", status_code=202)
def modules_remove(module_id: str, force: bool = Query(default=False), purge: bool = Query(default=False)):
    module_id = _validate_module_id(module_id)
    command = [str(SPAWNWP_CLI), "module", "remove", module_id]
    if force:
        command.append("--force")
    if purge:
        command.append("--purge")
    return _start_module_operation("uninstall", module_id, command)


@app.get("/api/modules/operations/{operation_id}")
def module_operation(operation_id: str):
    return _refresh_module_operation(_read_module_operation(operation_id))


@app.get("/", include_in_schema=False)
def cockpit_root():
    return RedirectResponse("/manage", status_code=307)


@app.get("/login", include_in_schema=False)
def cockpit_login():
    return login_page()


@app.get("/modules", include_in_schema=False)
def modules_page():
    return FileResponse(STATIC_DIR / "modules.html")


@app.get("/manage", include_in_schema=False)
def manage_page():
    return FileResponse(STATIC_DIR / "manage.html")


@app.get("/deploy", include_in_schema=False)
def deploy_page():
    return FileResponse(STATIC_DIR / "deploy.html")


@app.get("/updates", include_in_schema=False)
def updates_page():
    return FileResponse(STATIC_DIR / "updates.html")


@app.get("/system", include_in_schema=False)
def system_page():
    return FileResponse(STATIC_DIR / "system.html")


app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")
