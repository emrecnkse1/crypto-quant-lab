"""Genuinely read-only SQLite access for research inputs (FUNDING_RESEARCH_SPEC.md Bölüm 18.2).

`connect_read_only` opens an EXISTING database file through a SQLite URI with
`mode=ro` (the file is never created; every write fails with "attempt to
write a readonly database") and additionally sets `PRAGMA query_only=ON`.
No schema is created or migrated. The path is percent-encoded into the URI
so spaces, non-ASCII characters and URI-special characters (`?`, `#`, `%`)
name the file literally.

`read_snapshot` wraps a sequence of queries in ONE read transaction, so all
of them — including anything fingerprinted from them — see the same
committed state: SQLite gives snapshot isolation to a WAL reader and holds a
SHARED lock (blocking writers) in rollback-journal mode. The database is NOT
opened with `immutable=1`: it may be live, and SQLite must keep honoring
locks and the WAL. If a writer holds an exclusive lock for longer than the
busy timeout, the snapshot fails with a StorageError instead of reading an
inconsistent state.
"""

import sqlite3
import urllib.parse
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from crypto_quant_lab.storage.base import StorageError

BUSY_TIMEOUT_SECONDS = 5.0


def read_only_uri(path: str | Path) -> str:
    """The `file:` URI (mode=ro) naming exactly `path`, resolved to an absolute path."""
    absolute = Path(path).resolve().as_posix()
    if not absolute.startswith("/"):
        absolute = "/" + absolute  # Windows drive paths: file:///C:/...
    return f"file://{urllib.parse.quote(absolute, safe='/:')}?mode=ro"


def connect_read_only(path: str | Path) -> sqlite3.Connection:
    """Open an existing SQLite file read-only; a missing file raises FileNotFoundError."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"store file does not exist: {path.name}")
    try:
        connection = sqlite3.connect(
            read_only_uri(path),
            uri=True,
            timeout=BUSY_TIMEOUT_SECONDS,
            isolation_level=None,  # transactions only where read_snapshot asks
        )
    except sqlite3.Error as exc:
        raise StorageError(f"cannot open {path.name} read-only: {exc}") from exc
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except sqlite3.DatabaseError as exc:
        connection.close()
        raise StorageError(f"{path.name} is not a readable SQLite database: {exc}") from exc
    return connection


def existing_tables(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {row[0] for row in rows}


def require_tables(connection: sqlite3.Connection, required: tuple[str, ...], name: str) -> None:
    missing = [table for table in required if table not in existing_tables(connection)]
    if missing:
        raise StorageError(
            f"{name} lacks tables {missing}; read-only access never creates or migrates them"
        )


@contextmanager
def read_snapshot(connection: sqlite3.Connection) -> Iterator[None]:
    """One read transaction around the block (see module docstring)."""
    try:
        connection.execute("BEGIN")
        connection.execute("SELECT count(*) FROM sqlite_master").fetchone()  # takes the snapshot
    except sqlite3.Error as exc:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise StorageError(f"could not start a consistent read snapshot: {exc}") from exc
    try:
        yield
    finally:
        connection.execute("ROLLBACK")
