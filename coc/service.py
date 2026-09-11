"""The only write path into a KEENEYE workspace.

Every operation that changes state also appends a custody-log entry, inside the
same transaction.  That is the whole design: it is not possible to ingest
evidence, upload an attachment, verify a hash or transfer custody and end up
with the change recorded but the log entry missing, because either both land or
neither does.

Front-ends (the CLI, the JSON API) call into :class:`Workspace` and never touch
the database directly.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, BinaryIO

from coc import TOOL_NAME, __version__, auth, chain, db
from coc.hashing import ProgressCallback
from coc.storage import StorageError, StoredFile, Vault

# Every action the log can record.  Kept as constants so a typo becomes an
# ImportError rather than an unsearchable string in the audit trail.
ACTION_CASE_OPENED = "case_opened"
ACTION_CASE_CLOSED = "case_closed"
ACTION_EVIDENCE_INGESTED = "evidence_ingested"
ACTION_ATTACHMENT_UPLOADED = "attachment_uploaded"
ACTION_EVIDENCE_VIEWED = "evidence_viewed"
ACTION_EVIDENCE_DOWNLOADED = "evidence_downloaded"
ACTION_EVIDENCE_VERIFIED = "evidence_verified"
ACTION_CUSTODY_TRANSFERRED = "custody_transferred"
ACTION_CUSTODY_ACCEPTED = "custody_accepted"
ACTION_REPORT_GENERATED = "report_generated"
ACTION_USER_LOGIN = "user_login"
ACTION_USER_CREATED = "user_created"

ATTACHMENT_KINDS = ("photo", "document", "raw", "other")


class ServiceError(RuntimeError):
    """Raised when an operation is refused for a reason the caller can fix."""


def utc_now() -> str:
    """Current UTC time as an ISO-8601 string with a ``Z`` suffix."""
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass
class VerificationResult:
    """The outcome of re-hashing one evidence item."""

    evidence_id: int
    item_number: str
    original_sha256: str
    observed_sha256: str | None
    passed: bool
    reason: str | None = None
    checked_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "item_number": self.item_number,
            "original_sha256": self.original_sha256,
            "observed_sha256": self.observed_sha256,
            "passed": self.passed,
            "result": "pass" if self.passed else "fail",
            "reason": self.reason,
            "checked_at": self.checked_at,
        }


class Workspace:
    """A KEENEYE workspace: one SQLite database plus its evidence vault."""

    def __init__(self, root: str | Path) -> None:
        # Resolved eagerly: a workspace is identified by an absolute location.
        # Leaving it relative meant Flask's send_file re-resolved vault paths
        # against the application package directory instead of the cwd.
        self.root = Path(root).expanduser().resolve()
        self.db_path = self.root / db.DEFAULT_DB_FILENAME
        self.vault = Vault(self.root / "vault")
        # SQLite connections belong to the thread that opened them, and the web
        # server handles requests on several. Each thread therefore gets its own
        # connection to the same file; WAL mode lets readers run alongside the
        # single writer, and BEGIN IMMEDIATE serialises writers against one
        # another so the chain can never fork.
        self._local = threading.local()

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    @classmethod
    def initialize(cls, root: str | Path) -> Workspace:
        """Create a new workspace on disk, or open an existing one."""
        workspace = cls(root)
        workspace.root.mkdir(parents=True, exist_ok=True)
        workspace._local.connection = db.initialize(workspace.db_path)
        workspace.vault.ensure()
        return workspace

    @property
    def connection(self) -> sqlite3.Connection:
        """This thread's connection, opened on first use."""
        connection = getattr(self._local, "connection", None)
        if connection is None:
            if not self.db_path.is_file():
                raise ServiceError(
                    f"no KEENEYE workspace at {self.root} — run 'keeneye init' first"
                )
            connection = db.connect(self.db_path)
            self._local.connection = connection
        return connection

    def close(self) -> None:
        """Close this thread's connection, if it has one."""
        connection = getattr(self._local, "connection", None)
        if connection is not None:
            connection.close()
            self._local.connection = None

    def exists(self) -> bool:
        return self.db_path.is_file()

    # ------------------------------------------------------------------
    # logging
    # ------------------------------------------------------------------

    def _log(
        self,
        connection: sqlite3.Connection,
        action: str,
        actor: auth.Examiner | None,
        *,
        case_id: int | None = None,
        evidence_id: int | None = None,
        attachment_id: int | None = None,
        details: dict[str, Any] | None = None,
        hash_check_result: str = "n/a",
        observed_sha256: str | None = None,
        timestamp: str | None = None,
        transaction_held: bool = True,
    ) -> dict[str, Any]:
        """Append one custody entry.  Always called inside the caller's transaction."""
        return chain.append_entry(
            connection,
            {
                "case_id": case_id,
                "evidence_id": evidence_id,
                "attachment_id": attachment_id,
                "action": action,
                "actor": actor.id if actor else None,
                "actor_username": actor.username if actor else "system",
                "timestamp_utc": timestamp or utc_now(),
                "details_json": json.dumps(details or {}, sort_keys=True, separators=(",", ":")),
                "hash_check_result": hash_check_result,
                "observed_sha256": observed_sha256,
                "tool_name": TOOL_NAME,
                "tool_version": __version__,
            },
            _transaction_held=transaction_held,
        )

    def verify_chain(self) -> chain.ChainVerification:
        """Walk the entire custody log and confirm no link is broken."""
        return chain.verify(self.connection)

    def custody_entries(
        self,
        *,
        case_id: int | None = None,
        evidence_id: int | None = None,
        limit: int | None = None,
    ) -> Sequence[sqlite3.Row]:
        return chain.entries(
            self.connection, case_id=case_id, evidence_id=evidence_id, limit=limit
        )

    # ------------------------------------------------------------------
    # users
    # ------------------------------------------------------------------

    def create_user(
        self,
        username: str,
        password: str,
        *,
        full_name: str = "",
        role: str = "examiner",
        actor: auth.Examiner | None = None,
    ) -> auth.Examiner:
        username = username.strip()
        if not username:
            raise ServiceError("username must not be empty")
        if role not in auth.ROLES:
            raise ServiceError(f"role must be one of {', '.join(auth.ROLES)}")

        password_hash, salt, iterations = auth.hash_password(password)
        with db.immediate_transaction(self.connection) as connection:
            existing = connection.execute(
                "SELECT 1 FROM users WHERE username = ?", (username,)
            ).fetchone()
            if existing:
                raise ServiceError(f"user already exists: {username}")

            cursor = connection.execute(
                """INSERT INTO users
                   (username, full_name, role, password_hash, password_salt,
                    iterations, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (username, full_name, role, password_hash, salt, iterations, utc_now()),
            )
            user_id = int(cursor.lastrowid)
            created = auth.Examiner(
                id=user_id, username=username, full_name=full_name, role=role
            )
            self._log(
                connection,
                ACTION_USER_CREATED,
                actor or created,
                details={"username": username, "role": role},
            )
        return created

    def authenticate(self, username: str, password: str) -> auth.Examiner | None:
        """Check a credential and, on success, log the login."""
        row = self.connection.execute(
            "SELECT * FROM users WHERE username = ? AND active = 1", (username.strip(),)
        ).fetchone()
        if row is None:
            # Spend roughly the same time as a real check so that a missing
            # username is not distinguishable by response time alone.
            auth.hash_password(password, salt="00" * auth.SALT_BYTES)
            return None

        if not auth.verify_password(
            password, str(row["password_hash"]), str(row["password_salt"]), int(row["iterations"])
        ):
            return None

        examiner = auth.examiner_from_row(row)
        assert examiner is not None
        with db.immediate_transaction(self.connection) as connection:
            self._log(connection, ACTION_USER_LOGIN, examiner, details={"method": "access_key"})
        return examiner

    def get_user(self, user_id: int) -> auth.Examiner | None:
        return auth.examiner_from_row(
            self.connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        )

    def find_user(self, username: str) -> auth.Examiner | None:
        return auth.examiner_from_row(
            self.connection.execute(
                "SELECT * FROM users WHERE username = ?", (username.strip(),)
            ).fetchone()
        )

    def list_users(self) -> Sequence[sqlite3.Row]:
        return self.connection.execute(
            "SELECT id, username, full_name, role, created_at, active FROM users ORDER BY username"
        ).fetchall()

    # ------------------------------------------------------------------
    # cases
    # ------------------------------------------------------------------

    def open_case(
        self,
        case_number: str,
        title: str,
        actor: auth.Examiner,
        *,
        notes: str = "",
    ) -> sqlite3.Row:
        case_number = case_number.strip()
        if not case_number:
            raise ServiceError("case number must not be empty")

        with db.immediate_transaction(self.connection) as connection:
            if connection.execute(
                "SELECT 1 FROM cases WHERE case_number = ?", (case_number,)
            ).fetchone():
                raise ServiceError(f"case already exists: {case_number}")

            cursor = connection.execute(
                """INSERT INTO cases (case_number, title, opened_by, opened_at, notes)
                   VALUES (?, ?, ?, ?, ?)""",
                (case_number, title, actor.id, utc_now(), notes),
            )
            case_id = int(cursor.lastrowid)
            self._log(
                connection,
                ACTION_CASE_OPENED,
                actor,
                case_id=case_id,
                details={"case_number": case_number, "title": title},
            )
        return self.get_case(case_id)

    def close_case(self, case_id: int, actor: auth.Examiner) -> sqlite3.Row:
        with db.immediate_transaction(self.connection) as connection:
            connection.execute("UPDATE cases SET status = 'closed' WHERE id = ?", (case_id,))
            self._log(connection, ACTION_CASE_CLOSED, actor, case_id=case_id)
        return self.get_case(case_id)

    def get_case(self, identifier: int | str) -> sqlite3.Row:
        if isinstance(identifier, int) or str(identifier).isdigit():
            row = self.connection.execute(
                "SELECT * FROM cases WHERE id = ?", (int(identifier),)
            ).fetchone()
        else:
            row = self.connection.execute(
                "SELECT * FROM cases WHERE case_number = ?", (str(identifier),)
            ).fetchone()
        if row is None:
            raise ServiceError(f"no such case: {identifier}")
        return row

    def list_cases(self) -> Sequence[sqlite3.Row]:
        return self.connection.execute(
            """SELECT c.*,
                      (SELECT COUNT(*) FROM evidence e WHERE e.case_id = c.id)    AS evidence_count,
                      (SELECT COUNT(*) FROM attachments a WHERE a.case_id = c.id) AS attachment_count
               FROM cases c ORDER BY c.opened_at DESC"""
        ).fetchall()

    # ------------------------------------------------------------------
    # evidence
    # ------------------------------------------------------------------

    def _next_item_number(self, connection: sqlite3.Connection, case_id: int) -> str:
        count = connection.execute(
            "SELECT COUNT(*) FROM evidence WHERE case_id = ?", (case_id,)
        ).fetchone()[0]
        return f"ITEM-{int(count) + 1:03d}"

    def ingest_evidence(
        self,
        case_id: int,
        source: str | Path,
        actor: auth.Examiner,
        *,
        description: str = "",
        item_number: str | None = None,
        source_device: str = "",
        acquisition_date: str | None = None,
        original_filename: str | None = None,
        progress: ProgressCallback | None = None,
    ) -> sqlite3.Row:
        """Hash a file, admit it to the vault, and record it as evidence."""
        source = Path(source)
        stored = self.vault.store_file(source, progress=progress)
        return self._record_evidence(
            case_id,
            stored,
            actor,
            description=description,
            item_number=item_number,
            source_device=source_device,
            acquisition_date=acquisition_date,
            original_filename=original_filename or source.name,
        )

    def ingest_evidence_stream(
        self,
        case_id: int,
        stream: BinaryIO,
        actor: auth.Examiner,
        *,
        original_filename: str,
        total_size: int | None = None,
        description: str = "",
        item_number: str | None = None,
        source_device: str = "",
        acquisition_date: str | None = None,
        progress: ProgressCallback | None = None,
    ) -> sqlite3.Row:
        """Same as :meth:`ingest_evidence` for an upload that has no path."""
        stored = self.vault.store_stream(stream, total_size=total_size, progress=progress)
        return self._record_evidence(
            case_id,
            stored,
            actor,
            description=description,
            item_number=item_number,
            source_device=source_device,
            acquisition_date=acquisition_date,
            original_filename=original_filename,
        )

    def _record_evidence(
        self,
        case_id: int,
        stored: StoredFile,
        actor: auth.Examiner,
        *,
        description: str,
        item_number: str | None,
        source_device: str,
        acquisition_date: str | None,
        original_filename: str,
    ) -> sqlite3.Row:
        acquired = acquisition_date or date.today().isoformat()
        now = utc_now()

        with db.immediate_transaction(self.connection) as connection:
            if not connection.execute(
                "SELECT 1 FROM cases WHERE id = ?", (case_id,)
            ).fetchone():
                raise ServiceError(f"no such case: {case_id}")

            number = item_number or self._next_item_number(connection, case_id)
            cursor = connection.execute(
                """INSERT INTO evidence
                   (case_id, item_number, description, original_filename, byte_size,
                    sha256, sha1, vault_path, source_device, acquisition_date,
                    acquired_by, ingested_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    case_id,
                    number,
                    description,
                    original_filename,
                    stored.byte_size,
                    stored.digest.sha256,
                    stored.digest.sha1,
                    stored.relative_path,
                    source_device,
                    acquired,
                    actor.id,
                    now,
                ),
            )
            evidence_id = int(cursor.lastrowid)
            self._log(
                connection,
                ACTION_EVIDENCE_INGESTED,
                actor,
                case_id=case_id,
                evidence_id=evidence_id,
                details={
                    "item_number": number,
                    "original_filename": original_filename,
                    "byte_size": stored.byte_size,
                    "sha256": stored.digest.sha256,
                    "sha1": stored.digest.sha1,
                    "source_device": source_device,
                    "deduplicated": stored.deduplicated,
                },
                hash_check_result="pass",
                observed_sha256=stored.digest.sha256,
                timestamp=now,
            )
        return self.get_evidence(evidence_id)

    def get_evidence(self, evidence_id: int) -> sqlite3.Row:
        row = self.connection.execute(
            "SELECT * FROM evidence WHERE id = ?", (evidence_id,)
        ).fetchone()
        if row is None:
            raise ServiceError(f"no such evidence item: {evidence_id}")
        return row

    def list_evidence(self, case_id: int | None = None) -> Sequence[sqlite3.Row]:
        if case_id is None:
            return self.connection.execute(
                "SELECT * FROM evidence ORDER BY ingested_at DESC"
            ).fetchall()
        return self.connection.execute(
            "SELECT * FROM evidence WHERE case_id = ? ORDER BY item_number", (case_id,)
        ).fetchall()

    def verify_evidence(self, evidence_id: int, actor: auth.Examiner) -> VerificationResult:
        """Re-hash a stored evidence file and log the outcome either way.

        A failed check is recorded in the custody log exactly like a passing
        one.  An integrity tool that only writes down its successes is worse
        than useless, so the ``fail`` path here is deliberately not an
        exception the caller can swallow.
        """
        item = self.get_evidence(evidence_id)
        original = str(item["sha256"])
        checked_at = utc_now()
        observed: str | None = None
        reason: str | None = None

        try:
            observed = self.vault.rehash(original).sha256
            passed = observed == original
            if not passed:
                reason = "stored file no longer hashes to its recorded SHA-256"
        except StorageError as error:
            passed = False
            reason = str(error)

        with db.immediate_transaction(self.connection) as connection:
            self._log(
                connection,
                ACTION_EVIDENCE_VERIFIED,
                actor,
                case_id=int(item["case_id"]),
                evidence_id=evidence_id,
                details={
                    "item_number": str(item["item_number"]),
                    "original_sha256": original,
                    "observed_sha256": observed,
                    "reason": reason,
                },
                hash_check_result="pass" if passed else "fail",
                observed_sha256=observed,
                timestamp=checked_at,
            )

        return VerificationResult(
            evidence_id=evidence_id,
            item_number=str(item["item_number"]),
            original_sha256=original,
            observed_sha256=observed,
            passed=passed,
            reason=reason,
            checked_at=checked_at,
        )

    def verify_case(self, case_id: int, actor: auth.Examiner) -> list[VerificationResult]:
        return [
            self.verify_evidence(int(item["id"]), actor)
            for item in self.list_evidence(case_id)
        ]

    def record_access(
        self,
        evidence_id: int,
        actor: auth.Examiner,
        *,
        downloaded: bool = False,
    ) -> None:
        """Log that an examiner looked at or took a copy of an evidence item."""
        item = self.get_evidence(evidence_id)
        with db.immediate_transaction(self.connection) as connection:
            self._log(
                connection,
                ACTION_EVIDENCE_DOWNLOADED if downloaded else ACTION_EVIDENCE_VIEWED,
                actor,
                case_id=int(item["case_id"]),
                evidence_id=evidence_id,
                details={"item_number": str(item["item_number"])},
            )

    # ------------------------------------------------------------------
    # attachments — supporting photos, documents and raw files
    # ------------------------------------------------------------------

    def attach(
        self,
        case_id: int,
        source: str | Path | BinaryIO,
        actor: auth.Examiner,
        *,
        evidence_id: int | None = None,
        kind: str = "other",
        caption: str = "",
        original_filename: str | None = None,
        mime_type: str = "application/octet-stream",
        total_size: int | None = None,
        progress: ProgressCallback | None = None,
    ) -> sqlite3.Row:
        """Admit a supporting file — a scene photo, a warrant, a raw export.

        Attachments are hashed and vaulted exactly like evidence.  They are a
        separate table because they are *about* the evidence rather than being
        the evidence, and a court report has to be able to tell the difference.
        """
        if kind not in ATTACHMENT_KINDS:
            raise ServiceError(f"kind must be one of {', '.join(ATTACHMENT_KINDS)}")

        if isinstance(source, (str, Path)):
            path = Path(source)
            stored = self.vault.store_file(path, progress=progress)
            filename = original_filename or path.name
        else:
            stored = self.vault.store_stream(source, total_size=total_size, progress=progress)
            filename = original_filename or "upload.bin"

        now = utc_now()
        with db.immediate_transaction(self.connection) as connection:
            if not connection.execute(
                "SELECT 1 FROM cases WHERE id = ?", (case_id,)
            ).fetchone():
                raise ServiceError(f"no such case: {case_id}")
            if evidence_id is not None and not connection.execute(
                "SELECT 1 FROM evidence WHERE id = ? AND case_id = ?", (evidence_id, case_id)
            ).fetchone():
                raise ServiceError(f"evidence {evidence_id} is not part of case {case_id}")

            cursor = connection.execute(
                """INSERT INTO attachments
                   (case_id, evidence_id, kind, original_filename, mime_type,
                    byte_size, sha256, vault_path, caption, uploaded_by, uploaded_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    case_id,
                    evidence_id,
                    kind,
                    filename,
                    mime_type,
                    stored.byte_size,
                    stored.digest.sha256,
                    stored.relative_path,
                    caption,
                    actor.id,
                    now,
                ),
            )
            attachment_id = int(cursor.lastrowid)
            self._log(
                connection,
                ACTION_ATTACHMENT_UPLOADED,
                actor,
                case_id=case_id,
                evidence_id=evidence_id,
                attachment_id=attachment_id,
                details={
                    "kind": kind,
                    "original_filename": filename,
                    "mime_type": mime_type,
                    "byte_size": stored.byte_size,
                    "sha256": stored.digest.sha256,
                    "caption": caption,
                },
                hash_check_result="pass",
                observed_sha256=stored.digest.sha256,
                timestamp=now,
            )
        return self.get_attachment(attachment_id)

    def get_attachment(self, attachment_id: int) -> sqlite3.Row:
        row = self.connection.execute(
            "SELECT * FROM attachments WHERE id = ?", (attachment_id,)
        ).fetchone()
        if row is None:
            raise ServiceError(f"no such attachment: {attachment_id}")
        return row

    def list_attachments(
        self,
        *,
        case_id: int | None = None,
        evidence_id: int | None = None,
    ) -> Sequence[sqlite3.Row]:
        clauses: list[str] = []
        params: list[Any] = []
        if case_id is not None:
            clauses.append("case_id = ?")
            params.append(case_id)
        if evidence_id is not None:
            clauses.append("evidence_id = ?")
            params.append(evidence_id)
        sql = "SELECT * FROM attachments"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY uploaded_at ASC"
        return self.connection.execute(sql, params).fetchall()

    # ------------------------------------------------------------------
    # custody transfer
    # ------------------------------------------------------------------

    def transfer_custody(
        self,
        evidence_id: int,
        actor: auth.Examiner,
        recipient: auth.Examiner,
        *,
        reason: str = "",
    ) -> dict[str, Any]:
        """Hand an item to another examiner.

        Recorded as two entries — the handover and the acceptance — because a
        transfer nobody acknowledged is a gap in the chain, not a transfer.
        """
        item = self.get_evidence(evidence_id)
        if recipient.id == actor.id:
            raise ServiceError("custody cannot be transferred to the current holder")

        with db.immediate_transaction(self.connection) as connection:
            entry = self._log(
                connection,
                ACTION_CUSTODY_TRANSFERRED,
                actor,
                case_id=int(item["case_id"]),
                evidence_id=evidence_id,
                details={
                    "item_number": str(item["item_number"]),
                    "from": actor.username,
                    "to": recipient.username,
                    "reason": reason,
                    "accepted": False,
                },
            )
        return entry

    def accept_custody(self, evidence_id: int, actor: auth.Examiner) -> dict[str, Any]:
        """Acknowledge receipt of an item transferred to the current examiner."""
        item = self.get_evidence(evidence_id)
        pending = self.pending_transfer(evidence_id)
        if pending is None:
            raise ServiceError("no pending custody transfer for this item")
        if pending["to"] != actor.username:
            raise ServiceError(f"this item was transferred to {pending['to']}, not {actor.username}")

        with db.immediate_transaction(self.connection) as connection:
            entry = self._log(
                connection,
                ACTION_CUSTODY_ACCEPTED,
                actor,
                case_id=int(item["case_id"]),
                evidence_id=evidence_id,
                details={
                    "item_number": str(item["item_number"]),
                    "from": pending["from"],
                    "to": actor.username,
                    "transfer_seq": pending["seq"],
                },
            )
        return entry

    def pending_transfer(self, evidence_id: int) -> dict[str, Any] | None:
        """The most recent transfer for an item, if it has not been accepted."""
        rows = self.connection.execute(
            """SELECT seq, action, details_json FROM custody_log
               WHERE evidence_id = ? AND action IN (?, ?)
               ORDER BY seq DESC LIMIT 1""",
            (evidence_id, ACTION_CUSTODY_TRANSFERRED, ACTION_CUSTODY_ACCEPTED),
        ).fetchone()
        if rows is None or str(rows["action"]) != ACTION_CUSTODY_TRANSFERRED:
            return None
        details = json.loads(str(rows["details_json"]))
        details["seq"] = int(rows["seq"])
        return details

    def current_holder(self, evidence_id: int) -> str:
        """Whoever last took possession of an item, per the log."""
        row = self.connection.execute(
            """SELECT actor_username FROM custody_log
               WHERE evidence_id = ? AND action IN (?, ?)
               ORDER BY seq DESC LIMIT 1""",
            (evidence_id, ACTION_EVIDENCE_INGESTED, ACTION_CUSTODY_ACCEPTED),
        ).fetchone()
        return str(row["actor_username"]) if row else "unknown"

    # ------------------------------------------------------------------
    # aggregate views, used by the dashboard
    # ------------------------------------------------------------------

    def statistics(self) -> dict[str, Any]:
        cursor = self.connection.execute
        verification = self.verify_chain()
        last_check = cursor(
            """SELECT timestamp_utc, hash_check_result FROM custody_log
               WHERE action = ? ORDER BY seq DESC LIMIT 1""",
            (ACTION_EVIDENCE_VERIFIED,),
        ).fetchone()
        return {
            "cases": int(cursor("SELECT COUNT(*) FROM cases").fetchone()[0]),
            "open_cases": int(
                cursor("SELECT COUNT(*) FROM cases WHERE status = 'open'").fetchone()[0]
            ),
            "evidence": int(cursor("SELECT COUNT(*) FROM evidence").fetchone()[0]),
            "attachments": int(cursor("SELECT COUNT(*) FROM attachments").fetchone()[0]),
            "custody_entries": verification.entries_checked,
            "vault_bytes": self.vault.usage_bytes(),
            "chain": verification.as_dict(),
            "last_verification": {
                "timestamp_utc": str(last_check["timestamp_utc"]) if last_check else None,
                "result": str(last_check["hash_check_result"]) if last_check else None,
            },
        }


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    """Convert query results to plain dicts for JSON serialization."""
    return [dict(row) for row in rows]
