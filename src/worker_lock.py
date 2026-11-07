"""Host-local exclusive coordinators; never reset another local worker's leases."""
from __future__ import annotations

import hashlib
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def exclusive_worker(database: str, role: str, *, directory: Path | None = None):
    # Deliberately key by database name, not URI: localhost and 127.0.0.1
    # must not let two coordinators reset each other's work. This lock is
    # host-local; multiple crawler hosts sharing a database are not supported.
    root = directory or Path(tempfile.gettempdir()) / "mai-ir-worker-locks"
    root.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(f"{database}:{role}".encode()).hexdigest()
    with (root / f"{key}.lock").open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if not stream.tell():
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError(f"Процесс {role} для базы {database} уже запущен на этом компьютере.") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
