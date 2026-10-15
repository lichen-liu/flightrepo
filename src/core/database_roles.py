"""Database identity is stored in SQLite, never inferred from a filename."""

import sqlite3
from pathlib import Path


def is_ingestion(connection):
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_schema WHERE name='ingestion_store_metadata'"
        ).fetchone()
        is not None
    )


def sqlite_file(path):
    """Cheap identity probe; unrelated files must reach normal command guards."""
    path = Path(path)
    if not path.is_file():
        return False
    with path.open("rb") as handle:
        return handle.read(16) == b"SQLite format 3\x00"


def ingestion_path(path):
    path = Path(path)
    if not sqlite_file(path):
        return False
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        return is_ingestion(connection)


def require_serving(connection):
    if is_ingestion(connection):
        raise sqlite3.DatabaseError(
            "Ingestion database: serving readers and manual writers cannot use this file"
        )


def require_ingestion(connection):
    if not is_ingestion(connection):
        raise ValueError("Select an ingestion database initialized by python -m ingestion init")
    row = connection.execute("SELECT version FROM ingestion_store_metadata WHERE id=1").fetchone()
    if row is None or row[0] != 1:
        raise ValueError("Unsupported ingestion database identity/version")


def ingestion_target(path):
    """Check before downloads, initialization or any attempted ingestion write."""
    path = Path(path)
    if not path.is_file():
        return
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        tables = connection.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        if tables:
            require_ingestion(connection)
