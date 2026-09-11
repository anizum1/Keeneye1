"""Source file timestamps.

The property under test is that these survive ingest unchanged and land in the
*hashed* portion of the custody log — not that they are true. They are claims
made by the source machine and the tool must never present them otherwise.
"""

from __future__ import annotations

import json
import os

from coc import chain, db
from coc.metadata import SOURCE_BROWSER, SOURCE_FILESYSTEM, from_browser, read_source_timestamps

# 2019-02-12T19:33:20Z — comfortably before any test run.
OLD_EPOCH = 1_550_000_000
OLD_ISO = "2019-02-12T19:33:20Z"


def _aged(path, epoch: int = OLD_EPOCH):
    os.utime(path, (epoch, epoch))
    return path


def test_modification_time_is_read_from_the_file(tmp_path):
    path = tmp_path / "old.pdf"
    path.write_bytes(b"x")
    _aged(path)
    assert read_source_timestamps(path).modified_at == OLD_ISO


def test_missing_file_yields_empty_rather_than_raising(tmp_path):
    result = read_source_timestamps(tmp_path / "absent.bin")
    assert result.is_empty


def test_filesystem_readings_are_labelled_as_such(tmp_path):
    path = tmp_path / "a.bin"
    path.write_bytes(b"x")
    assert read_source_timestamps(path).reported_by == SOURCE_FILESYSTEM


def test_browser_readings_are_labelled_differently(tmp_path):
    """A client's claim must not be indistinguishable from a stat."""
    result = from_browser(OLD_EPOCH * 1000)
    assert result.modified_at == OLD_ISO
    assert result.reported_by == SOURCE_BROWSER


def test_browser_garbage_is_survived(tmp_path):
    for value in (None, "", "not-a-number", []):
        assert from_browser(value).is_empty


def test_ingest_records_the_source_time_not_the_ingest_time(
    workspace, examiner, case, tmp_path
):
    path = tmp_path / "contract.pdf"
    path.write_bytes(b"%PDF")
    _aged(path)

    item = workspace.ingest_evidence(int(case["id"]), path, examiner)

    assert item["source_modified_at"] == OLD_ISO
    assert item["source_reported_by"] == SOURCE_FILESYSTEM
    assert item["ingested_at"] != OLD_ISO       # the two are genuinely different


def test_attachments_carry_source_times_too(workspace, examiner, case, tmp_path):
    photo = tmp_path / "site.jpg"
    photo.write_bytes(b"\xff\xd8")
    _aged(photo)

    attachment = workspace.attach(int(case["id"]), photo, examiner, kind="photo")
    assert attachment["source_modified_at"] == OLD_ISO


def test_source_times_land_in_the_hashed_part_of_the_log(
    workspace, examiner, case, tmp_path
):
    """They live in details_json, which is one of chain.CHAINED_FIELDS."""
    path = tmp_path / "a.bin"
    path.write_bytes(b"x")
    _aged(path)
    item = workspace.ingest_evidence(int(case["id"]), path, examiner)

    entry = workspace.custody_entries(evidence_id=int(item["id"]))[0]
    details = json.loads(str(entry["details_json"]))
    assert details["source_modified_at"] == OLD_ISO

    # And therefore covered: recomputing over altered details breaks the hash.
    tampered = dict(entry)
    tampered["details_json"] = str(entry["details_json"]).replace(OLD_ISO, "2026-01-01T00:00:00Z")
    assert chain.compute_entry_hash(tampered, str(entry["prev_hash"])) != str(entry["entry_hash"])


def test_a_stream_upload_without_times_is_still_accepted(workspace, examiner, case):
    import io

    item = workspace.ingest_evidence_stream(
        int(case["id"]), io.BytesIO(b"data"), examiner, original_filename="upload.bin"
    )
    assert item["source_modified_at"] is None
    assert workspace.verify_chain().ok


# ----------------------------------------------------------------------
# schema migration
# ----------------------------------------------------------------------


def test_v1_database_migrates_without_breaking_its_chain(workspace, examiner, case, tmp_path):
    """The whole risk of a schema change: silently invalidating history."""
    path = tmp_path / "a.bin"
    path.write_bytes(b"x")
    workspace.ingest_evidence(int(case["id"]), path, examiner)
    head_before = workspace.verify_chain().head_hash
    workspace.close()

    # Rewind to v1 and drop the columns added in v2, as an old database would be.
    raw = db.connect(workspace.db_path)
    for table in ("evidence", "attachments"):
        for column in db._V2_COLUMNS:
            raw.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    raw.execute("PRAGMA user_version = 1")
    raw.close()

    migrated = db.initialize(workspace.db_path)

    assert db.schema_version(migrated) == db.SCHEMA_VERSION
    result = chain.verify(migrated)
    assert result.ok
    assert result.head_hash == head_before          # history survived untouched
    assert migrated.execute("SELECT source_modified_at FROM evidence").fetchone()[0] is None


def test_migration_is_idempotent(workspace):
    before = db.schema_version(workspace.connection)
    db.migrate(workspace.connection)
    db.migrate(workspace.connection)
    assert db.schema_version(workspace.connection) == before
