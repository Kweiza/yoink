"""flock-based file lock with timeout."""
from __future__ import annotations
import contextlib
import fcntl
import time
from pathlib import Path

class LockTimeout(RuntimeError): ...

@contextlib.contextmanager
def acquire(path: Path, timeout: float):
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    fd = open(path, "a+")
    try:
        while True:
            try:
                fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise LockTimeout(f"lock {path} not acquired within {timeout}s")
                time.sleep(0.05)
        yield
    finally:
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
        finally:
            fd.close()
