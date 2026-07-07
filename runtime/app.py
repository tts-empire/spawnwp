import asyncio
import base64
import json
import os
import posixpath
import re
import shlex
import shutil
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from auth import initialize as initialize_auth
from auth import is_enrolled, login_page, router as auth_router, session as auth_session, valid_csrf
from ingest import router as ingest_router

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
                     "/api/images/delete", "/api/images/refresh", "/api/blueprint-pairings"}
FILE_WRITE_RE = re.compile(r"^/api/files/[^/]+/(write|upload|delete|rename|mkdir)$")


def requires_recent_auth(path: str) -> bool:
    return path in DESTRUCTIVE_PATHS or bool(FILE_WRITE_RE.match(path))


@app.middleware("http")
async def application_authentication(request: Request, call_next):
    path = request.url.path
    public = path in {
        "/login", "/api/version", "/api/auth/state", "/api/auth/setup/start",
        "/api/auth/setup/finish", "/api/auth/passkey/start", "/api/auth/passkey/finish",
        "/api/auth/fallback",
    } or path.startswith("/api/ingest/")  # signed-request auth lives in ingest.py
    active = None if public else auth_session(request)
    if not public and not active:
        if path.startswith("/api/"):
            response = JSONResponse({"detail": "Authentication required"}, status_code=401)
        else:
            response = RedirectResponse("/login", status_code=303)
    elif active and request.method in {"POST", "PUT", "PATCH", "DELETE"} and not valid_csrf(request, active):
        response = JSONResponse({"detail": "Invalid CSRF token"}, status_code=403)
    elif active and request.method == "POST" and requires_recent_auth(path) and int(__import__("time").time()) - active["recent_auth"] > 600:
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
TEMPLATE_MARKER = PRIMARY_PROJECT / ".spawnwp" / "template-only"
BLUEPRINT_TOOL = PRIMARY_PROJECT / "scripts" / "blueprint.py"
PHP_SWITCH_TOOL = PRIMARY_PROJECT / "scripts" / "php-switch-progress.py"
SPAWNWP_CLI = Path("/usr/local/bin/spawnwp")
SPAWNWP_VERSION = Path("/var/lib/spawnwp/VERSION")
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
    result = subprocess.run(
        ["python3", str(BLUEPRINT_TOOL), "list"],
        capture_output=True, text=True, cwd=PRIMARY_PROJECT,
    )
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
SNAP_RE = re.compile(r'^\d{8}-\d{6}$')   # timestamp snapshot: YYYYMMDD-HHMMSS

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

# ── API ────────────────────────────────────────────────────────────────────────

@app.get("/api/version")
def version_info():
    version = SPAWNWP_VERSION.read_text().strip() if SPAWNWP_VERSION.is_file() else "0.1.0"
    return {"version": version}


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
        if env.get("SPAWNWP_EXPIRES", "").isdigit():
            import time as _time
            expires_at = int(env["SPAWNWP_EXPIRES"])
            days_left = max(0, round((expires_at - _time.time()) / 86400, 1))

        result.append({
            "name": proj.name,
            "url": env.get("WP_HOME", ""),
            "expires_at": expires_at,
            "days_left": days_left,
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


@app.get("/api/snapshots/{project}")
def list_snapshots(project: str):
    """List of the site's snapshots: name (timestamp), DB size, files present."""
    proj = resolve_project(project)
    db_dir = proj / "backups" / "db"
    files_dir = proj / "backups" / "files"
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
                "db_kb": st.st_size // 1024,
                "has_files": has_files,
                "files_kb": files_kb,
                "mtime": int(st.st_mtime),
            })
    snaps.sort(key=lambda s: s["name"], reverse=True)   # most recent first
    return snaps


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
    name: str
    blueprint: str = "development"
    php_version: str | None = None
    wordpress_version: str | None = None   # override the blueprint's pinned WP version (e.g. "latest")
    php_settings: PhpIniSettings | None = None
    lifetime_days: int = 0   # 0 = permanent; otherwise the site self-destructs
    install_deploy_plugin: bool = False   # opt-in: bundle the SpawnWP Deploy plugin
    deactivate_plugins: bool = False   # captured blueprints: leave plugins inactive

@app.post("/api/new-project")
def new_project(body: NewProject):
    if not SLUG_RE.match(body.name):
        raise HTTPException(400, "Invalid name: use lowercase letters, digits and hyphens only")
    if not SLUG_RE.match(body.blueprint):
        raise HTTPException(400, "Invalid blueprint id")
    if not 0 <= body.lifetime_days <= 365:
        raise HTTPException(400, "lifetime_days must be between 0 and 365")
    validate_blueprint_choice(body.blueprint, body.php_version, body.wordpress_version)
    guard_not_busy()
    if is_project(PROJECTS_ROOT / body.name):
        raise HTTPException(409, f"Project '{body.name}' already exists")
    env = body.php_settings.validated().as_env() if body.php_settings else {}
    if body.lifetime_days:
        env["SPAWNWP_SITE_LIFETIME_DAYS"] = str(body.lifetime_days)
    if body.install_deploy_plugin:
        env["SPAWNWP_INSTALL_DEPLOY_PLUGIN"] = "1"
    if body.deactivate_plugins:
        env["SPAWNWP_DEACTIVATE_PLUGINS"] = "1"
    return sse_response(
        ["bash", str(PRIMARY_PROJECT / "scripts" / "new-project.sh"), body.name, body.blueprint, body.php_version or "", body.wordpress_version or ""],
        PRIMARY_PROJECT,
        env or None,
    )


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
# destruction is done by the hourly spawnwp-site-expiry timer via site-expiry.sh.

class SiteExpiry(BaseModel):
    lifetime_days: int   # counted from now; 0 = make the site permanent


@app.post("/api/expiry/{project}")
def set_expiry(project: str, body: SiteExpiry):
    proj = resolve_project(project)
    if proj == PRIMARY_PROJECT:
        raise HTTPException(400, "The primary stack cannot expire")
    if not 0 <= body.lifetime_days <= 365:
        raise HTTPException(400, "lifetime_days must be between 0 and 365")
    env_file = proj / ".env"
    lines = [l for l in env_file.read_text().splitlines() if not l.startswith("SPAWNWP_EXPIRES=")]
    if body.lifetime_days:
        import time as _time
        lines.append(f"SPAWNWP_EXPIRES={int(_time.time()) + body.lifetime_days * 86400}")
    env_file.write_text("\n".join(lines) + "\n")
    return {"project": proj.name, "lifetime_days": body.lifetime_days}


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

CONFIG_ENV = Path(os.environ.get("SPAWNWP_CONFIG_ENV", "/etc/spawnwp/config.env"))
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


def _php_versions_in_use() -> dict[str, list[str]]:
    used: dict[str, list[str]] = {}
    for proj in get_projects():
        ver = _read_env(proj).get("PHP_VERSION", "")
        if ver:
            used.setdefault(ver, []).append(proj.name)
    return used


@app.get("/api/images")
def list_images():
    stale_days = int(_config_env_get("SPAWNWP_IMAGE_MAX_AGE_DAYS", "7") or 7)
    used = _php_versions_in_use()
    images = []
    raw = run(["docker", "image", "ls", PHP_IMAGE_REPO, "--format", "json"], PRIMARY_PROJECT)
    for line in raw.splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        tag = entry.get("Tag", "")
        if not PHP_VER_RE.match(tag):
            continue
        created = run(["docker", "image", "inspect", "-f", "{{.Created}}",
                       f"{PHP_IMAGE_REPO}:{tag}"], PRIMARY_PROJECT)
        age = _image_age_days(created)
        images.append({
            "tag": f"{PHP_IMAGE_REPO}:{tag}",
            "php_version": tag,
            "size_gb": round(_parse_size(entry.get("Size", "0B")), 2),
            "age_days": age,
            "stale": age >= stale_days,
            "used_by": sorted(used.get(tag, [])),
        })
    images.sort(key=lambda i: i["php_version"])
    return {"images": images, "stale_days": stale_days}


class ImageDelete(BaseModel):
    php_version: str
    confirm: str   # must match php_version (guards against accidental click)


@app.post("/api/images/delete")
def delete_image(body: ImageDelete):
    if not PHP_VER_RE.match(body.php_version):
        raise HTTPException(400, "Invalid PHP version")
    if body.confirm != body.php_version:
        raise HTTPException(400, "Confirmation does not match")
    guard_not_busy()
    users = _php_versions_in_use().get(body.php_version, [])
    if users:
        raise HTTPException(409, f"Image in use by: {', '.join(sorted(users))}. "
                                 "Destroy or switch those sites first.")
    tag = f"{PHP_IMAGE_REPO}:{body.php_version}"
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


# ── Cockpit pages and shared assets ───────────────────────────────────────────

STATIC_DIR = Path(os.environ.get("SPAWNWP_STATIC_DIR", "/srv/wp-cockpit/static"))


@app.get("/", include_in_schema=False)
def cockpit_root():
    return RedirectResponse("/manage", status_code=307)


@app.get("/login", include_in_schema=False)
def cockpit_login():
    return login_page()


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
