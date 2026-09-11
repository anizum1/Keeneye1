"""The hash-chained custody log.

Each entry carries the hash of the entry before it, exactly the linking idea a
blockchain uses::

    entry_hash = SHA256(canonical_json(entry_fields) || prev_hash)

Editing any historical entry changes its ``entry_hash``, which breaks the
``prev_hash`` of every entry that follows.  One altered row is therefore not a
quiet edit — it is a visible fracture at a known position, which is what
:func:`verify` reports.

Two details matter for this to hold up over time:

* The hashed field list is written out explicitly in :data:`CHAINED_FIELDS`.
  Serializing whatever columns happen to exist would mean that adding a column
  later silently invalidates every chain written before it.
* Serialization is canonical (sorted keys, no incidental whitespace, UTF-8), so
  the same entry always produces the same bytes on any machine.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from coc.db import immediate_transaction
from coc.hashing import ZERO_DIGEST

#: Exactly the fields that are covered by ``entry_hash``, in a fixed order.
#: Never reorder or remove an entry here without a migration plan: doing so
#: invalidates every chain written under the old definition.
CHAINED_FIELDS: tuple[str, ...] = (
    "seq",
    "case_id",
    "evidence_id",
    "attachment_id",
    "action",
    "actor",
    "actor_username",
    "timestamp_utc",
    "details_json",
    "hash_check_result",
    "observed_sha256",
    "tool_name",
    "tool_version",
)


class ChainError(RuntimeError):
    """Raised when the custody log cannot be appended to safely."""


def canonical_payload(entry: Mapping[str, Any] | sqlite3.Row) -> str:
    """Serialize the hashed fields of an entry deterministically.

    Accepts a plain mapping or a :class:`sqlite3.Row` so that an entry can be
    hashed identically whether it is about to be written or was just read back.
    """
    if isinstance(entry, sqlite3.Row):
        entry = dict(entry)
    payload = {name: entry.get(name) for name in CHAINED_FIELDS}
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def compute_entry_hash(entry: Mapping[str, Any] | sqlite3.Row, prev_hash: str) -> str:
    """Hash one entry against its predecessor's hash."""
    material = canonical_payload(entry).encode("utf-8") + prev_hash.encode("ascii")
    return hashlib.sha256(material).hexdigest()


def head(connection: sqlite3.Connection) -> sqlite3.Row | None:
    """The most recent entry in the chain, or ``None`` when it is empty."""
    return connection.execute(
        "SELECT * FROM custody_log ORDER BY seq DESC LIMIT 1"
    ).fetchone()


def append_entry(
    connection: sqlite3.Connection,
    entry: Mapping[str, Any],
    *,
    _transaction_held: bool = False,
) -> dict[str, Any]:
    """Append one entry to the chain and return it as stored.

    ``seq`` is assigned here rather than by ``AUTOINCREMENT`` because it is one
    of the hashed fields: the value has to be known before the hash can be
    computed.  Holding the write lock for the whole read-tail-then-insert makes
    that assignment safe against concurrent writers.

    Pass ``_transaction_held=True`` when the caller already holds an immediate
    transaction, so a multi-step operation (write a row *and* log it) commits
    or rolls back as a single unit.
    """

    def _do(conn: sqlite3.Connection) -> dict[str, Any]:
        tail = head(conn)
        next_seq = 1 if tail is None else int(tail["seq"]) + 1
        prev_hash = ZERO_DIGEST if tail is None else str(tail["entry_hash"])

        record: dict[str, Any] = {name: entry.get(name) for name in CHAINED_FIELDS}
        record["seq"] = next_seq
        record.setdefault("details_json", "{}")
        if record.get("details_json") is None:
            record["details_json"] = "{}"
        if record.get("hash_check_result") is None:
            record["hash_check_result"] = "n/a"

        record["prev_hash"] = prev_hash
        record["entry_hash"] = compute_entry_hash(record, prev_hash)

        columns = list(CHAINED_FIELDS) + ["prev_hash", "entry_hash"]
        placeholders = ", ".join(f":{name}" for name in columns)
        conn.execute(
            f"INSERT INTO custody_log ({', '.join(columns)}) VALUES ({placeholders})",
            record,
        )
        return record

    if _transaction_held:
        return _do(connection)
    with immediate_transaction(connection) as conn:
        return _do(conn)


@dataclass
class ChainVerification:
    """The outcome of walking the whole chain."""

    ok: bool
    entries_checked: int
    head_hash: str | None = None
    first_broken_seq: int | None = None
    reason: str | None = None
    broken_entries: list[int] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "INTACT" if self.ok else "BROKEN"

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "status": self.status,
            "entries_checked": self.entries_checked,
            "head_hash": self.head_hash,
            "first_broken_seq": self.first_broken_seq,
            "reason": self.reason,
            "broken_entries": list(self.broken_entries),
        }


def verify(connection: sqlite3.Connection) -> ChainVerification:
    """Walk every entry and confirm the chain is unbroken.

    Checks, in order of what they catch: that sequence numbers are contiguous
    (a removed entry), that each entry points at its predecessor's hash (a
    reordered or inserted entry), and that each stored hash still matches a
    recomputation of its own contents (an edited entry).
    """
    rows = connection.execute("SELECT * FROM custody_log ORDER BY seq ASC").fetchall()
    if not rows:
        return ChainVerification(ok=True, entries_checked=0, head_hash=None)

    broken: list[int] = []
    first_reason: str | None = None
    expected_prev = ZERO_DIGEST

    for position, row in enumerate(rows, start=1):
        seq = int(row["seq"])
        failure: str | None = None

        if seq != position:
            failure = (
                f"sequence break: expected entry {position}, found {seq} "
                "(an entry is missing or was renumbered)"
            )
        elif str(row["prev_hash"]) != expected_prev:
            failure = (
                f"entry {seq} points at predecessor {str(row['prev_hash'])[:16]}… "
                f"but the previous entry hashes to {expected_prev[:16]}…"
            )
        else:
            recomputed = compute_entry_hash(row, str(row["prev_hash"]))
            if recomputed != str(row["entry_hash"]):
                failure = (
                    f"entry {seq} has been altered: contents hash to "
                    f"{recomputed[:16]}… but the log records {str(row['entry_hash'])[:16]}…"
                )

        if failure is not None:
            broken.append(seq)
            if first_reason is None:
                first_reason = failure

        expected_prev = str(row["entry_hash"])

    return ChainVerification(
        ok=not broken,
        entries_checked=len(rows),
        head_hash=str(rows[-1]["entry_hash"]),
        first_broken_seq=broken[0] if broken else None,
        reason=first_reason,
        broken_entries=broken,
    )


def entries(
    connection: sqlite3.Connection,
    *,
    case_id: int | None = None,
    evidence_id: int | None = None,
    limit: int | None = None,
) -> Sequence[sqlite3.Row]:
    """Read custody entries, optionally narrowed to a case or evidence item."""
    clauses: list[str] = []
    params: list[Any] = []
    if case_id is not None:
        clauses.append("case_id = ?")
        params.append(case_id)
    if evidence_id is not None:
        clauses.append("evidence_id = ?")
        params.append(evidence_id)

    sql = "SELECT * FROM custody_log"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY seq ASC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)

    return connection.execute(sql, params).fetchall()
