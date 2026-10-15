"""Database-owned disk policy; raw archives and daily staging are excluded."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def control(path, *, writable=False):
    mode = "rw" if writable else "ro"
    connection = sqlite3.connect(
        Path(path).resolve().as_uri() + f"?mode={mode}", uri=True, timeout=10
    )
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def policy(path):
    if not Path(path).is_file():
        return None
    with control(path) as connection:
        if not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='database_policy'"
        ).fetchone():
            return None
        row = connection.execute("SELECT id,quota_bytes FROM database_policy WHERE id=1").fetchone()
        return dict(row) if row is not None else None


def file_bytes(path):
    try:
        return path.stat().st_size if path.is_file() else 0
    except FileNotFoundError:
        return 0


def database_bytes(path):
    path = Path(path)
    return sum(file_bytes(p) for p in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")))


def set_quota(path, quota_bytes):
    if quota_bytes is not None and (type(quota_bytes) is not int or quota_bytes < 0):
        raise ValueError("Quota must be non-negative bytes or None")
    with control(path, writable=True) as connection:
        if (
            connection.execute(
                "UPDATE database_policy SET quota_bytes=? WHERE id=1", (quota_bytes,)
            ).rowcount
            != 1
        ):
            raise ValueError("Initialize database policy with maintenance init first")


def storage_status(path):
    state = policy(path)
    if state is None:
        raise ValueError("Initialize database policy with maintenance init first")
    used = database_bytes(path)
    quota = state["quota_bytes"]
    return {
        "database": str(Path(path).resolve()),
        "quota_bytes": quota,
        "used_bytes": used,
        "over_quota_bytes": max(0, used - quota) if quota is not None else 0,
        "durable_records": "protected; ingestion pauses when over quota",
    }
