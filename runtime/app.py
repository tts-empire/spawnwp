import asyncio
import json
import os
import re
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

@asynccontextmanager
async def lifespan(_app: FastAPI):
    initialize_auth()
    yield


app = FastAPI(docs_url=None, redoc_url=None, lifespan=lifespan)
app.include_router(auth_router)


@app.middleware("http")
async def application_authentication(request: Request, call_next):
    path = request.url.path
    public = path in {
        "/login", "/api/version", "/api/auth/state", "/api/auth/setup/start",
        "/api/auth/setup/finish", "/api/auth/passkey/start", "/api/auth/passkey/finish",
        "/api/auth/fallback",
    }
    active = None if public else auth_session(request)
    if not public and not active:
        if path.startswith("/api/"):
            return JSONResponse({"detail": "Authentication required"}, status_code=401)
        return RedirectResponse("/login", status_code=303)
    if active and request.method in {"POST", "PUT", "PATCH", "DELETE"} and not valid_csrf(request, active):
        return JSONResponse({"detail": "Invalid CSRF token"}, status_code=403)
    destructive = {"/api/destroy", "/api/restore", "/api/php-switch"}
    if active and request.method == "POST" and path in destructive and int(__import__("time").time()) - active["recent_auth"] > 600:
        return JSONResponse({"detail": "Recent authentication required; sign out and sign in again"}, status_code=403)
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), publickey-credentials-get=(self), publickey-credentials-create=(self)")
    if path == "/login":
        response.headers.setdefault("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; frame-ancestors 'none'")
    return response

# ── Sliding-window session keep-alive ───────────────────────────────────────────
# The port-knock authorizes the IP; the reaper (cockpit-reaper.timer) revokes it
# after 30 min of inactivity. Every request refreshes the session file timestamp
# so, as long as the cockpit tab is open (auto-refresh every 30s), the IP stays
# authorized. We only touch files that already exist (sessions opened by a knock).
SESSIONS_DIR = Path("/run/cockpit-sessions")

@app.middleware("http")
async def keepalive_session(request: Request, call_next):
    ip = request.headers.get("x-real-ip")
    if not ip:
        fwd = request.headers.get("x-forwarded-for", "")
        ip = fwd.split(",")[0].strip() if fwd else None
    if ip:
        try:
            f = SESSIONS_DIR / ip
            if f.exists():
                f.touch()
        except OSError:
            pass
    return await call_next(request)

# ── Constants ──────────────────────────────────────────────────────────────────

PROJECTS_ROOT = Path("/srv")
PRIMARY_PROJECT = PROJECTS_ROOT / "wp-dev"
BLUEPRINT_TOOL = PRIMARY_PROJECT / "scripts" / "blueprint.py"
SPAWNWP_CLI = Path("/usr/local/bin/spawnwp")
SPAWNWP_VERSION = Path("/var/lib/spawnwp/VERSION")

# Every project dir contains a compose.yaml and a Makefile
def is_project(p: Path) -> bool:
    return p.is_dir() and (p / "compose.yaml").exists() and (p / "Makefile").exists()

def get_projects() -> list[Path]:
    dirs = sorted([PRIMARY_PROJECT] + [
        p for p in PROJECTS_ROOT.iterdir()
        if p != PRIMARY_PROJECT and is_project(p)
    ])
    return [d for d in dirs if is_project(d)]


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


def validate_blueprint_choice(blueprint_id: str, php_version: str | None) -> None:
    cmd = ["python3", str(BLUEPRINT_TOOL), "resolve", blueprint_id]
    if php_version:
        cmd.extend(["--php", php_version])
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=PRIMARY_PROJECT)
    if result.returncode != 0:
        raise HTTPException(400, result.stderr.strip().removeprefix("ERROR: ").strip())

SLUG_RE = re.compile(r'^[a-z0-9][a-z0-9\-]{0,30}$')
SNAP_RE = re.compile(r'^\d{8}-\d{6}$')   # timestamp snapshot: YYYYMMDD-HHMMSS

# ── Helpers ────────────────────────────────────────────────────────────────────

def run(cmd: list[str], cwd: Path) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    return (result.stdout + result.stderr).strip()

async def stream_command(cmd: list[str], cwd: Path) -> AsyncIterator[str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=cwd,
    )
    assert proc.stdout
    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        yield f"data: {json.dumps(line.decode(errors='replace').rstrip())}\n\n"
    await proc.wait()
    rc = proc.returncode
    yield f"data: {json.dumps(f'__EXIT__{rc}')}\n\n"

def sse_response(cmd: list[str], cwd: Path) -> StreamingResponse:
    return StreamingResponse(
        stream_command(cmd, cwd),
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

        result.append({
            "name": proj.name,
            "url": env.get("WP_HOME", ""),
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
    return sse_response(["make", "-s", "php-switch", f"VER={body.version}"], proj)


@app.get("/api/blueprints")
def list_blueprints():
    return blueprint_catalog()


class NewProject(BaseModel):
    name: str
    blueprint: str = "development"
    php_version: str | None = None

@app.post("/api/new-project")
def new_project(body: NewProject):
    if not SLUG_RE.match(body.name):
        raise HTTPException(400, "Invalid name: use lowercase letters, digits and hyphens only")
    if not SLUG_RE.match(body.blueprint):
        raise HTTPException(400, "Invalid blueprint id")
    validate_blueprint_choice(body.blueprint, body.php_version)
    guard_not_busy()
    if is_project(PROJECTS_ROOT / body.name):
        raise HTTPException(409, f"Project '{body.name}' already exists")
    return sse_response(
        ["bash", str(PRIMARY_PROJECT / "scripts" / "new-project.sh"), body.name, body.blueprint, body.php_version or ""],
        PRIMARY_PROJECT,
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
    Behind the 3 layers (knock+BasicAuth+HTTPS); whoever reaches this endpoint
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


app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")
