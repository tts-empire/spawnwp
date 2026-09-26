"""Host-wide exclusion shared by HTTP mutations and durable automation jobs."""
import fcntl
import os
from contextvars import ContextVar
from pathlib import Path

from fastapi import HTTPException

current = ContextVar("spawnwp_operation_lock", default=None)


def child_options(env=None):
    fd = current.get()
    return {"env": {**os.environ, **(env or {}), **({"SPAWNWP_PROJECT_LOCK_HELD": "1"} if fd is not None else {})},
            "pass_fds": (fd,) if fd is not None else ()}


def acquire():
    path = Path(os.environ.get("SPAWNWP_OPERATION_LOCK", "/run/lock/spawnwp-projects.lock"))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise HTTPException(409, "Another site operation is running; retry later")
    return fd


def release(fd):
    if fd is not None:
        os.close(fd)
