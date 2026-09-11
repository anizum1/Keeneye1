"""SQLite storage for cases, evidence, attachments and the custody log.

Two things here are load-bearing and worth reading closely:

``custody_log`` is append-only.  Triggers abort any ``UPDATE`` or ``DELETE``
against it.  That stops the *application* from rewriting history; it does not
stop somebody with a copy of the database file and a shell.  Detecting that is
the job of the hash chain in :mod:`coc.chain`, not of these triggers.

The schema is versioned via ``PRAGMA user_version`` so a future migration has
somewhere to hook into rather than silently running against an old shape.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 2

DEFAULT_DB_FILENAME = "keeneye.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT    NOT NULL UNIQUE,
    full_name       TEXT    NOT NULL DEFAULT '',
    role            TEXT    NOT NULL DEFAULT 'examiner'
                            CHECK (role IN ('admin', 'examiner')),
    password_hash   TEXT    NOT NULL,
    password_salt   TEXT    NOT NULL,
    iterations      INTEGER NOT NULL,
    created_at      TEXT    NOT NULL,
    active          INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
);

CREATE TABLE IF NOT EXISTS cases (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    case_number     TEXT    NOT NULL UNIQUE,
    title           TEXT    NOT NULL,
    opened_by       INTEGER NOT NULL REFERENCES users(id),
    opened_at       TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'open'
                            CHECK (status IN ('open', 'closed', 'archived')),
    notes           TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS evidence (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id             INTEGER NOT NULL REFERENCES cases(id),
    item_number         TEXT    NOT NULL,
    description         TEXT    NOT NULL DEFAULT '',
    original_filename   TEXT    NOT NULL,
    byte_size           INTEGER NOT NULL,
    sha256              TEXT    NOT NULL,
    sha1                TEXT,
    vault_path          TEXT    NOT NULL,
    source_device       TEXT    NOT NULL DEFAULT '',
    acquisition_date    TEXT    NOT NULL,
    acquired_by         INTEGER NOT NULL REFERENCES users(id),
    ingested_at         TEXT    NOT NULL,
    -- Times the SOURCE system claimed, read once before vaulting. Claims, not
    -- facts: see coc/metadata.py. NULL where the platform does not keep them.
    source_modified_at  TEXT,
    source_created_at   TEXT,
    source_reported_by  TEXT,
    UNIQUE (case_id, item_number)
);

CREATE TABLE IF NOT EXISTS attachments (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id             INTEGER NOT NULL REFERENCES cases(id),
    evidence_id         INTEGER REFERENCES evidence(id),
    kind                TEXT    NOT NULL DEFAULT 'other'
                                CHECK (kind IN ('photo', 'document', 'raw', 'other')),
    original_filename   TEXT    NOT NULL,
    mime_type           TEXT    NOT NULL DEFAULT 'application/octet-stream',
    byte_size           INTEGER NOT NULL,
    sha256              TEXT    NOT NULL,
    vault_path          TEXT    NOT NULL,
    caption             TEXT    NOT NULL DEFAULT '',
    uploaded_by         INTEGER NOT NULL REFERENCES users(id),
    uploaded_at         TEXT    NOT NULL,
    source_modified_at  TEXT,
    source_created_at   TEXT,
    source_reported_by  TEXT
);

-- Append-only.  See the module docstring and the triggers below.
CREATE TABLE IF NOT EXISTS custody_log (
    seq                 INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id             INTEGER REFERENCES cases(id),
    evidence_id         INTEGER REFERENCES evidence(id),
    attachment_id       INTEGER REFERENCES attachments(id),
    action              TEXT    NOT NULL,
    actor               INTEGER REFERENCES users(id),
    actor_username      TEXT    NOT NULL,
    timestamp_utc       TEXT    NOT NULL,
    details_json        TEXT    NOT NULL DEFAULT '{}',
    hash_check_result   TEXT    NOT NULL DEFAULT 'n/a'
                                CHECK (hash_check_result IN ('pass', 'fail', 'n/a')),
    observed_sha256     TEXT,
    tool_name           TEXT    NOT NULL,
    tool_version        TEXT    NOT NULL,
    prev_hash           TEXT    NOT NULL,
    entry_hash          TEXT    NOT NULL UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_evidence_case      ON evidence(case_id);
CREATE INDEX IF NOT EXISTS idx_attachments_case   ON attachments(case_id);
CREATE INDEX IF NOT EXISTS idx_attachments_ev     ON attachments(evidence_id);
CREATE INDEX IF NOT EXISTS idx_log_case           ON custody_log(case_id);
CREATE INDEX IF NOT EXISTS idx_log_evidence       ON custody_log(evidence_id);

-- The application must never rewrite the audit trail.  Enforced in the
-- database rather than in Python so that a bug in the service layer cannot
-- quietly bypass it.
CREATE TRIGGER IF NOT EXISTS custody_log_no_update
BEFORE UPDATE ON custody_log
BEGIN
    SELECT RAISE(ABORT, 'custody_log is append-only: UPDATE is not permitted');
END;

CREATE TRIGGER IF NOT EXISTS custody_log_no_delete
BEFORE DELETE ON custody_log
BEGIN
    SELECT RAISE(ABORT, 'custody_log is append-only: DELETE is not permitted');
END;
"""


def connect(db_path: str | Path, *, read_only: bool = False) -> sqlite3.Connection:
    """Open a connection with the pragmas this application depends on."""
    db_path = Path(db_path)
    connection = sqlite3.connect(
        db_path,
        isolation_level=None,  # explicit transactions; see chain.append_entry
        timeout=30.0,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    if not read_only:
        connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = FULL")
    return connection


#: Columns added after v1. Only ever *added* — never changed or removed, because
#: the custody chain is only compatible across versions if old data stays valid.
_V2_COLUMNS = ("source_modified_at", "source_created_at", "source_reported_by")


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def migrate(connection: sqlite3.Connection) -> int:
    """Bring an existing database up to the current schema version.

    Only additive, nullable columns are added, so existing rows stay valid and —
    critically — the custody chain is untouched. The chain hashes
    ``chain.CHAINED_FIELDS`` on the custody_log table, which this never alters;
    a schema change that invalidated historical chains would be catastrophic and
    silent, so the migration is deliberately kept to something that cannot.

    Returns the version migrated from.
    """
    was = schema_version(connection)

    if was < 2:
        for table in ("evidence", "attachments"):
            existing = _columns(connection, table)
            for column in _V2_COLUMNS:
                if column not in existing:
                    connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")

    if was != SCHEMA_VERSION:
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    return was


def initialize(db_path: str | Path) -> sqlite3.Connection:
    """Create the database and schema if needed, and migrate an existing one."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = connect(db_path)
    connection.executescript(SCHEMA)
    migrate(connection)
    return connection


def schema_version(connection: sqlite3.Connection) -> int:
    return int(connection.execute("PRAGMA user_version").fetchone()[0])


@contextmanager
def immediate_transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a block inside ``BEGIN IMMEDIATE``.

    The custody log's tail must be read and the next entry written without
    another writer slipping in between, or two entries would claim the same
    predecessor and the chain would fork.  ``BEGIN IMMEDIATE`` takes the write
    lock up front, which is what makes that read-then-write pair atomic.
    """
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
    except Exception:
        connection.execute("ROLLBACK")
        raise
    connection.execute("COMMIT")
