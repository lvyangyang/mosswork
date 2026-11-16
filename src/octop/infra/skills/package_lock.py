"""Serialize package mutations across executor threads and server processes."""

from __future__ import annotations

import hashlib
import os
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_guard = threading.Lock()
_locks: dict[str, threading.RLock] = {}


@contextmanager
def package_lock(root: Path, package_id: str) -> Iterator[None]:
    # Keep locks outside content trees: directory swaps must not replace a lock.
    directory = root.parent / f".{root.name}-locks"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{hashlib.sha256(package_id.encode()).hexdigest()}.lock"
    with _guard:
        lock = _locks.setdefault(str(path.resolve()), threading.RLock())
    with lock, path.open("a+b") as handle:
        if sys.platform == "win32":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if not handle.tell():
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if sys.platform == "win32":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
