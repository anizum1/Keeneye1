"""Bulk import, verified backup, and examiner lifecycle.

These are the commands that stand between the tool and real day-to-day use, so
the tests concentrate on the ways they could quietly do damage: importing twice,
a backup that restores to something broken, and locking everyone out.
"""

from __future__ import annotations

import json
import os

import pytest

from coc import chain, db
from coc.cli import EXIT_ERROR, EXIT_INTEGRITY, EXIT_OK, main
from coc.service import ServiceError, Workspace


@pytest.fixture()
def ws(tmp_path):
    root = str(tmp_path / "ws")
    main(["--workspace", root, "init"])
    main(["--workspace", root, "user", "add", "--username", "kim",
          "--role", "admin", "--access-key", "open-sesame"])
    main(["--workspace", root, "case", "open", "2026-100", "Vendor dispute", "--as", "kim"])
    return root


@pytest.fixture()
def documents(tmp_path):
    """A folder shaped like real work: nested, mixed, with clutter."""
    root = tmp_path / "docs"
    (root / "contracts").mkdir(parents=True)
    (root / "photos").mkdir()
    (root / ".hidden").mkdir()

    (root / "contracts" / "msa.pdf").write_bytes(b"%PDF msa")
    (root / "contracts" / "amendment.pdf").write_bytes(b"%PDF amendment")
    (root / "photos" / "site.jpg").write_bytes(b"\xff\xd8 site")
    (root / "notes.txt").write_bytes(b"notes")
    (root / ".hidden" / "junk.tmp").write_bytes(b"junk")
    (root / "Thumbs.db").write_bytes(b"thumbs")
    return root


# ----------------------------------------------------------------------
# import
# ----------------------------------------------------------------------


def test_dry_run_writes_absolutely_nothing(ws, documents):
    assert main(["--workspace", ws, "import", str(documents), "--case", "2026-100",
                 "--recursive", "--dry-run", "--as", "kim"]) == EXIT_OK

    workspace = Workspace(ws)
    assert list(workspace.list_evidence()) == []
    assert workspace.vault.usage_bytes() == 0


def test_import_walks_nested_folders_and_skips_clutter(ws, documents):
    main(["--workspace", ws, "import", str(documents), "--case", "2026-100",
          "--recursive", "--as", "kim"])

    names = {str(row["original_filename"]) for row in Workspace(ws).list_evidence()}
    assert names == {"msa.pdf", "amendment.pdf", "site.jpg", "notes.txt"}
    assert "junk.tmp" not in names      # hidden directory
    assert "Thumbs.db" not in names     # platform noise


def test_import_preserves_the_folder_structure_in_the_description(ws, documents):
    main(["--workspace", ws, "import", str(documents), "--case", "2026-100",
          "--recursive", "--as", "kim"])

    descriptions = {str(row["description"]) for row in Workspace(ws).list_evidence()}
    assert "contracts/msa.pdf" in descriptions


def test_without_recursive_only_the_top_level_is_taken(ws, documents):
    main(["--workspace", ws, "import", str(documents), "--case", "2026-100", "--as", "kim"])
    assert {str(r["original_filename"]) for r in Workspace(ws).list_evidence()} == {"notes.txt"}


def test_reimporting_the_same_folder_adds_nothing(ws, documents):
    """The failure mode this prevents: one document, four item numbers."""
    args = ["--workspace", ws, "import", str(documents), "--case", "2026-100",
            "--recursive", "--as", "kim"]
    main(args)
    workspace = Workspace(ws)
    before = len(workspace.list_evidence())
    head = workspace.verify_chain().head_hash
    workspace.close()

    assert main(args) == EXIT_OK

    after = Workspace(ws)
    assert len(after.list_evidence()) == before
    assert after.verify_chain().head_hash == head   # nothing was written at all


def test_import_as_attachments_binds_them_to_an_item(ws, documents, tmp_path):
    evidence = tmp_path / "image.dd"
    evidence.write_bytes(b"disk")
    main(["--workspace", ws, "evidence", "add", str(evidence), "--case", "2026-100", "--as", "kim"])

    main(["--workspace", ws, "import", str(documents / "photos"), "--case", "2026-100",
          "--mode", "photo", "--evidence", "1", "--as", "kim"])

    attachments = Workspace(ws).list_attachments(evidence_id=1)
    assert len(attachments) == 1
    assert str(attachments[0]["kind"]) == "photo"


def test_import_captures_source_timestamps(ws, documents):
    target = documents / "notes.txt"
    os.utime(target, (1_550_000_000, 1_550_000_000))
    main(["--workspace", ws, "import", str(documents), "--case", "2026-100", "--as", "kim"])

    row = Workspace(ws).list_evidence()[0]
    assert str(row["source_modified_at"]) == "2019-02-12T19:33:20Z"


def test_importing_a_missing_folder_fails_cleanly(ws, tmp_path):
    assert main(["--workspace", ws, "import", str(tmp_path / "nope"),
                 "--case", "2026-100", "--as", "kim"]) == EXIT_ERROR


# ----------------------------------------------------------------------
# backup
# ----------------------------------------------------------------------


def _populate(ws, tmp_path):
    source = tmp_path / "disk.dd"
    source.write_bytes(b"\x00EVIDENCE\xff" * 500)
    main(["--workspace", ws, "evidence", "add", str(source), "--case", "2026-100", "--as", "kim"])


def test_backup_produces_a_workspace_that_verifies(ws, tmp_path):
    _populate(ws, tmp_path)
    out = tmp_path / "backup"

    assert main(["--workspace", ws, "backup", "--out", str(out), "--deep"]) == EXIT_OK
    assert main(["--workspace", str(out), "chain", "verify"]) == EXIT_OK

    live = Workspace(ws).verify_chain()
    copy = Workspace(str(out)).verify_chain()
    assert copy.head_hash == live.head_hash
    assert copy.entries_checked == live.entries_checked


def test_backup_manifest_pins_the_chain_head(ws, tmp_path):
    _populate(ws, tmp_path)
    out = tmp_path / "backup"
    main(["--workspace", ws, "backup", "--out", str(out)])

    manifest = json.loads((out / "MANIFEST.json").read_text())
    assert manifest["chain_head"] == Workspace(ws).verify_chain().head_hash
    assert manifest["chain_status"] == "INTACT"
    assert manifest["evidence"] == 1


def test_backup_refuses_to_write_inside_the_live_workspace(ws, tmp_path):
    _populate(ws, tmp_path)
    assert main(["--workspace", ws, "backup", "--out", f"{ws}/inside"]) == EXIT_ERROR


def test_backup_refuses_a_non_empty_destination(ws, tmp_path):
    _populate(ws, tmp_path)
    out = tmp_path / "backup"
    out.mkdir()
    (out / "something.txt").write_text("occupied")
    assert main(["--workspace", ws, "backup", "--out", str(out)]) == EXIT_ERROR


def test_a_corrupted_backup_is_caught_by_verification(ws, tmp_path):
    """A backup nobody checked is not a backup."""
    _populate(ws, tmp_path)
    out = tmp_path / "backup"
    main(["--workspace", ws, "backup", "--out", str(out)])

    stored = Workspace(str(out))
    path = stored.vault.absolute_path_for(str(stored.get_evidence(1)["sha256"]))
    os.chmod(path, 0o644)
    path.write_bytes(b"corrupted in transit")

    assert main(["--workspace", str(out), "verify", "--evidence", "1", "--as", "kim"]) == EXIT_INTEGRITY


def test_backup_of_a_broken_chain_still_succeeds_but_says_so(ws, tmp_path):
    """A broken log is exactly the thing you want a copy of."""
    _populate(ws, tmp_path)
    workspace = Workspace(ws)
    workspace.close()

    raw = db.connect(f"{ws}/keeneye.sqlite")
    raw.execute("DROP TRIGGER IF EXISTS custody_log_no_update")
    raw.execute("UPDATE custody_log SET actor_username = 'ghost' WHERE seq = 3")
    raw.close()

    out = tmp_path / "backup"
    assert main(["--workspace", ws, "backup", "--out", str(out)]) == EXIT_OK
    assert not chain.verify(db.connect(out / "keeneye.sqlite")).ok


# ----------------------------------------------------------------------
# examiner lifecycle
# ----------------------------------------------------------------------


def test_deactivated_examiner_cannot_sign_in(workspace, examiner, second_examiner):
    assert workspace.authenticate("raj", "another-passphrase") is not None
    workspace.set_user_active(second_examiner.id, False, examiner)
    assert workspace.authenticate("raj", "another-passphrase") is None


def test_reactivation_restores_access(workspace, examiner, second_examiner):
    workspace.set_user_active(second_examiner.id, False, examiner)
    workspace.set_user_active(second_examiner.id, True, examiner)
    assert workspace.authenticate("raj", "another-passphrase") is not None


def test_you_cannot_lock_yourself_out(workspace, examiner):
    with pytest.raises(ServiceError, match="your own account"):
        workspace.set_user_active(examiner.id, False, examiner)


def test_the_last_administrator_cannot_be_removed(workspace, examiner, second_examiner):
    """Otherwise the workspace becomes unadministrable with no way back."""
    with pytest.raises(ServiceError, match="last active administrator"):
        workspace.set_user_active(examiner.id, False, second_examiner)


def test_deactivation_is_recorded_in_the_chain(workspace, examiner, second_examiner):
    workspace.set_user_active(second_examiner.id, False, examiner)

    actions = [str(entry["action"]) for entry in workspace.custody_entries()]
    assert "user_deactivated" in actions
    assert workspace.verify_chain().ok


def test_history_of_a_deactivated_examiner_survives(
    workspace, examiner, second_examiner, case, evidence_file
):
    """Accounts are disabled, never deleted — entries must stay attributable."""
    item = workspace.ingest_evidence(int(case["id"]), evidence_file, second_examiner)
    workspace.set_user_active(second_examiner.id, False, examiner)

    entry = workspace.custody_entries(evidence_id=int(item["id"]))[0]
    assert str(entry["actor_username"]) == "raj"
    assert workspace.get_user(second_examiner.id) is not None
