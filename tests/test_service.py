"""The service layer — specifically, that nothing changes without being logged."""

from __future__ import annotations

import pytest

from coc import service
from coc.service import ServiceError, Workspace


def _actions(workspace: Workspace) -> list[str]:
    return [str(entry["action"]) for entry in workspace.custody_entries()]


# ----------------------------------------------------------------------
# users and authentication
# ----------------------------------------------------------------------


def test_creating_a_user_is_logged(workspace: Workspace, examiner):
    assert service.ACTION_USER_CREATED in _actions(workspace)


def test_duplicate_username_is_refused(workspace: Workspace, examiner):
    with pytest.raises(ServiceError, match="already exists"):
        workspace.create_user("kim", "another-password")


def test_authentication_round_trip(workspace: Workspace, examiner):
    assert workspace.authenticate("kim", "s3cret-passphrase") is not None
    assert workspace.authenticate("kim", "wrong") is None
    assert workspace.authenticate("nobody", "wrong") is None


def test_successful_login_is_logged_and_failures_are_not(workspace: Workspace, examiner):
    workspace.authenticate("kim", "wrong")
    assert service.ACTION_USER_LOGIN not in _actions(workspace)

    workspace.authenticate("kim", "s3cret-passphrase")
    assert service.ACTION_USER_LOGIN in _actions(workspace)


def test_unknown_role_is_refused(workspace: Workspace):
    with pytest.raises(ServiceError, match="role must be"):
        workspace.create_user("x", "pw", role="superuser")


# ----------------------------------------------------------------------
# evidence intake
# ----------------------------------------------------------------------


def test_ingest_hashes_on_arrival_and_logs_it(workspace: Workspace, examiner, case, evidence_file):
    item = workspace.ingest_evidence(int(case["id"]), evidence_file, examiner)

    assert item["sha256"]
    assert item["item_number"] == "ITEM-001"
    assert item["original_filename"] == "disk.dd"

    entry = workspace.custody_entries(evidence_id=int(item["id"]))[0]
    assert entry["action"] == service.ACTION_EVIDENCE_INGESTED
    assert entry["observed_sha256"] == item["sha256"]
    assert entry["hash_check_result"] == "pass"


def test_item_numbers_increment_per_case(workspace: Workspace, examiner, case, tmp_path):
    numbers = []
    for index in range(3):
        source = tmp_path / f"item{index}.bin"
        source.write_bytes(f"content {index}".encode())
        numbers.append(workspace.ingest_evidence(int(case["id"]), source, examiner)["item_number"])

    assert numbers == ["ITEM-001", "ITEM-002", "ITEM-003"]


def test_ingest_into_a_missing_case_is_refused(workspace: Workspace, examiner, evidence_file):
    with pytest.raises(ServiceError, match="no such case"):
        workspace.ingest_evidence(999, evidence_file, examiner)


def test_tool_name_and_version_are_stamped_on_every_entry(workspace: Workspace, examiner, case):
    for entry in workspace.custody_entries():
        assert entry["tool_name"] == "KEENEYE"
        assert entry["tool_version"]


# ----------------------------------------------------------------------
# attachments
# ----------------------------------------------------------------------


def test_attaching_a_photo_is_hashed_and_logged(workspace: Workspace, examiner, case, tmp_path):
    photo = tmp_path / "scene.jpg"
    photo.write_bytes(b"\xff\xd8\xff\xe0 scene photo")

    attachment = workspace.attach(
        int(case["id"]), photo, examiner, kind="photo",
        caption="Laptop in situ", mime_type="image/jpeg",
    )

    assert attachment["kind"] == "photo"
    assert attachment["sha256"]
    assert service.ACTION_ATTACHMENT_UPLOADED in _actions(workspace)


def test_attachment_can_be_bound_to_an_evidence_item(
    workspace: Workspace, examiner, case, evidence_file, tmp_path
):
    item = workspace.ingest_evidence(int(case["id"]), evidence_file, examiner)
    doc = tmp_path / "warrant.pdf"
    doc.write_bytes(b"%PDF-1.7 warrant")

    attachment = workspace.attach(
        int(case["id"]), doc, examiner, evidence_id=int(item["id"]), kind="document"
    )

    assert attachment["evidence_id"] == item["id"]
    assert len(workspace.list_attachments(evidence_id=int(item["id"]))) == 1


def test_attachment_must_belong_to_the_same_case(
    workspace: Workspace, examiner, case, evidence_file, tmp_path
):
    item = workspace.ingest_evidence(int(case["id"]), evidence_file, examiner)
    other = workspace.open_case("2026-015", "Unrelated", examiner)
    doc = tmp_path / "x.pdf"
    doc.write_bytes(b"x")

    with pytest.raises(ServiceError, match="not part of case"):
        workspace.attach(int(other["id"]), doc, examiner, evidence_id=int(item["id"]))


def test_unknown_attachment_kind_is_refused(workspace: Workspace, examiner, case, tmp_path):
    doc = tmp_path / "x.bin"
    doc.write_bytes(b"x")
    with pytest.raises(ServiceError, match="kind must be"):
        workspace.attach(int(case["id"]), doc, examiner, kind="malware")


# ----------------------------------------------------------------------
# re-verification
# ----------------------------------------------------------------------


def test_verification_passes_for_an_untouched_file(
    workspace: Workspace, examiner, case, evidence_file
):
    item = workspace.ingest_evidence(int(case["id"]), evidence_file, examiner)
    result = workspace.verify_evidence(int(item["id"]), examiner)

    assert result.passed
    assert result.observed_sha256 == item["sha256"]


def test_verification_fails_loudly_when_the_vault_copy_is_altered(
    workspace: Workspace, examiner, case, evidence_file
):
    import os

    item = workspace.ingest_evidence(int(case["id"]), evidence_file, examiner)
    stored = workspace.vault.absolute_path_for(str(item["sha256"]))
    os.chmod(stored, 0o644)
    stored.write_bytes(b"tampered")

    result = workspace.verify_evidence(int(item["id"]), examiner)

    assert not result.passed
    assert result.observed_sha256 != item["sha256"]
    assert "no longer hashes" in (result.reason or "")


def test_a_failed_check_is_recorded_in_the_log(
    workspace: Workspace, examiner, case, evidence_file
):
    """An integrity tool that only logs its successes is worthless."""
    import os

    item = workspace.ingest_evidence(int(case["id"]), evidence_file, examiner)
    stored = workspace.vault.absolute_path_for(str(item["sha256"]))
    os.chmod(stored, 0o644)
    stored.write_bytes(b"tampered")
    workspace.verify_evidence(int(item["id"]), examiner)

    checks = [
        entry for entry in workspace.custody_entries()
        if entry["action"] == service.ACTION_EVIDENCE_VERIFIED
    ]
    assert checks[-1]["hash_check_result"] == "fail"
    assert workspace.verify_chain().ok  # the log itself is still sound


def test_verification_reports_a_missing_vault_file(
    workspace: Workspace, examiner, case, evidence_file
):
    import os

    item = workspace.ingest_evidence(int(case["id"]), evidence_file, examiner)
    stored = workspace.vault.absolute_path_for(str(item["sha256"]))
    os.chmod(stored, 0o644)
    os.unlink(stored)

    result = workspace.verify_evidence(int(item["id"]), examiner)
    assert not result.passed
    assert "missing from vault" in (result.reason or "")


# ----------------------------------------------------------------------
# custody transfer
# ----------------------------------------------------------------------


def test_transfer_then_accept_moves_the_holder(
    workspace: Workspace, examiner, second_examiner, case, evidence_file
):
    item = workspace.ingest_evidence(int(case["id"]), evidence_file, examiner)
    evidence_id = int(item["id"])

    assert workspace.current_holder(evidence_id) == "kim"

    workspace.transfer_custody(evidence_id, examiner, second_examiner, reason="analysis")
    assert workspace.pending_transfer(evidence_id)["to"] == "raj"
    assert workspace.current_holder(evidence_id) == "kim"  # not yet acknowledged

    workspace.accept_custody(evidence_id, second_examiner)
    assert workspace.pending_transfer(evidence_id) is None
    assert workspace.current_holder(evidence_id) == "raj"


def test_only_the_named_recipient_can_accept(
    workspace: Workspace, examiner, second_examiner, case, evidence_file
):
    item = workspace.ingest_evidence(int(case["id"]), evidence_file, examiner)
    workspace.transfer_custody(int(item["id"]), examiner, second_examiner)

    with pytest.raises(ServiceError, match="was transferred to raj"):
        workspace.accept_custody(int(item["id"]), examiner)


def test_accepting_without_a_transfer_is_refused(
    workspace: Workspace, examiner, case, evidence_file
):
    item = workspace.ingest_evidence(int(case["id"]), evidence_file, examiner)
    with pytest.raises(ServiceError, match="no pending custody transfer"):
        workspace.accept_custody(int(item["id"]), examiner)


def test_self_transfer_is_refused(workspace: Workspace, examiner, case, evidence_file):
    item = workspace.ingest_evidence(int(case["id"]), evidence_file, examiner)
    with pytest.raises(ServiceError, match="current holder"):
        workspace.transfer_custody(int(item["id"]), examiner, examiner)


# ----------------------------------------------------------------------
# the invariant everything else exists to protect
# ----------------------------------------------------------------------


def test_chain_stays_intact_across_a_full_workflow(
    workspace: Workspace, examiner, second_examiner, case, evidence_file, tmp_path
):
    case_id = int(case["id"])
    item = workspace.ingest_evidence(case_id, evidence_file, examiner)
    evidence_id = int(item["id"])

    photo = tmp_path / "scene.jpg"
    photo.write_bytes(b"\xff\xd8 photo")
    workspace.attach(case_id, photo, examiner, evidence_id=evidence_id, kind="photo")

    workspace.verify_evidence(evidence_id, examiner)
    workspace.record_access(evidence_id, examiner)
    workspace.transfer_custody(evidence_id, examiner, second_examiner)
    workspace.accept_custody(evidence_id, second_examiner)
    workspace.close_case(case_id, second_examiner)

    result = workspace.verify_chain()
    assert result.ok
    assert result.entries_checked == len(workspace.custody_entries())


def test_a_failed_write_leaves_no_orphan_log_entry(workspace: Workspace, examiner, evidence_file):
    """The row and its log entry share one transaction, so neither lands alone."""
    before = len(workspace.custody_entries())

    with pytest.raises(ServiceError):
        workspace.ingest_evidence(999, evidence_file, examiner)

    assert len(workspace.custody_entries()) == before
    assert workspace.verify_chain().ok


def test_statistics_reflect_the_workspace(
    workspace: Workspace, examiner, case, evidence_file, tmp_path
):
    photo = tmp_path / "p.jpg"
    photo.write_bytes(b"\xff\xd8")
    workspace.ingest_evidence(int(case["id"]), evidence_file, examiner)
    workspace.attach(int(case["id"]), photo, examiner, kind="photo")

    stats = workspace.statistics()
    assert stats["cases"] == 1
    assert stats["evidence"] == 1
    assert stats["attachments"] == 1
    assert stats["chain"]["status"] == "INTACT"
    assert stats["vault_bytes"] > 0
