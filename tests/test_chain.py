"""The hash chain, and what it catches.

These tests deliberately reach past the service layer with a raw sqlite3
connection and drop the append-only triggers, because that is exactly what an
attacker with filesystem access would do.  The point of the chain is that doing
so is still detectable.
"""

from __future__ import annotations

import sqlite3

import pytest

from coc import chain, db
from coc.hashing import ZERO_DIGEST
from coc.service import Workspace


def _append(workspace: Workspace, count: int) -> None:
    for index in range(count):
        chain.append_entry(
            workspace.connection,
            {
                "action": f"action_{index}",
                "actor_username": "kim",
                "timestamp_utc": f"2026-09-11T0{index}:00:00Z",
                "tool_name": "KEENEYE",
                "tool_version": "0.1.0",
            },
        )


def _raw_edit(db_path, *statements: str) -> None:
    """Modify the log the way someone with the database file would."""
    raw = sqlite3.connect(db_path)
    raw.execute("DROP TRIGGER IF EXISTS custody_log_no_update")
    raw.execute("DROP TRIGGER IF EXISTS custody_log_no_delete")
    for statement in statements:
        raw.execute(statement)
    raw.commit()
    raw.close()


def test_empty_chain_is_intact(workspace: Workspace):
    result = workspace.verify_chain()
    assert result.ok
    assert result.entries_checked == 0
    assert result.head_hash is None


def test_genesis_entry_points_at_zero(workspace: Workspace):
    _append(workspace, 1)
    entry = workspace.custody_entries()[0]
    assert entry["prev_hash"] == ZERO_DIGEST
    assert entry["seq"] == 1


def test_each_entry_links_to_its_predecessor(workspace: Workspace):
    _append(workspace, 6)
    entries = workspace.custody_entries()

    for previous, current in zip(entries, entries[1:], strict=False):
        assert current["prev_hash"] == previous["entry_hash"]

    assert workspace.verify_chain().ok


def test_canonical_payload_is_order_independent():
    a = {"action": "x", "seq": 1, "actor": 2}
    b = {"actor": 2, "seq": 1, "action": "x"}
    assert chain.canonical_payload(a) == chain.canonical_payload(b)


def test_hash_covers_every_chained_field():
    """Changing any hashed field must change the hash."""
    base = dict.fromkeys(chain.CHAINED_FIELDS, "value")
    baseline = chain.compute_entry_hash(base, ZERO_DIGEST)

    for name in chain.CHAINED_FIELDS:
        mutated = dict(base)
        mutated[name] = "tampered"
        assert chain.compute_entry_hash(mutated, ZERO_DIGEST) != baseline, name


def test_hash_covers_the_predecessor():
    entry = dict.fromkeys(chain.CHAINED_FIELDS, "value")
    assert chain.compute_entry_hash(entry, ZERO_DIGEST) != chain.compute_entry_hash(
        entry, "f" * 64
    )


def test_editing_an_entry_is_detected_at_the_exact_position(workspace: Workspace):
    _append(workspace, 5)
    workspace.close()

    _raw_edit(workspace.db_path, "UPDATE custody_log SET action = 'WIPED' WHERE seq = 3")

    result = chain.verify(db.connect(workspace.db_path))
    assert not result.ok
    assert result.first_broken_seq == 3
    assert "altered" in (result.reason or "")


def test_deleting_an_entry_is_detected(workspace: Workspace):
    _append(workspace, 5)
    workspace.close()

    _raw_edit(workspace.db_path, "DELETE FROM custody_log WHERE seq = 3")

    result = chain.verify(db.connect(workspace.db_path))
    assert not result.ok
    assert result.first_broken_seq == 4
    assert "sequence break" in (result.reason or "")


def test_truncating_the_tail_is_detected_only_by_the_head_hash(workspace: Workspace):
    """Chopping entries off the end leaves a valid-looking prefix.

    This is the known limit of a bare hash chain: it proves nothing was edited,
    not that nothing was removed from the end.  The head hash has to be recorded
    somewhere outside the database for that, which is what a court report does.
    """
    _append(workspace, 5)
    full_head = workspace.verify_chain().head_hash
    workspace.close()

    _raw_edit(workspace.db_path, "DELETE FROM custody_log WHERE seq > 3")

    result = chain.verify(db.connect(workspace.db_path))
    assert result.ok  # the surviving prefix is internally consistent
    assert result.head_hash != full_head  # but the head no longer matches the report


def test_append_only_triggers_block_the_application(workspace: Workspace):
    _append(workspace, 2)

    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        workspace.connection.execute("UPDATE custody_log SET action = 'x' WHERE seq = 1")

    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        workspace.connection.execute("DELETE FROM custody_log WHERE seq = 1")


def test_sequence_numbers_are_contiguous_from_one(workspace: Workspace):
    _append(workspace, 10)
    seqs = [int(entry["seq"]) for entry in workspace.custody_entries()]
    assert seqs == list(range(1, 11))
