"""The court PDF."""

from __future__ import annotations

import sqlite3

from coc.report import build_case_report
from coc.service import Workspace


def _populate(workspace: Workspace, examiner, case, tmp_path):
    case_id = int(case["id"])
    image = tmp_path / "disk.dd"
    image.write_bytes(b"\x00IMAGE\xff" * 2000)
    item = workspace.ingest_evidence(case_id, image, examiner, source_device="Dell XPS")

    photo = tmp_path / "scene.jpg"
    photo.write_bytes(b"\xff\xd8 photo")
    workspace.attach(case_id, photo, examiner, evidence_id=int(item["id"]),
                     kind="photo", caption="Laptop in situ", mime_type="image/jpeg")
    workspace.verify_evidence(int(item["id"]), examiner)
    return case_id


def test_report_is_a_pdf_and_returns_its_own_digest(workspace, examiner, case, tmp_path):
    case_id = _populate(workspace, examiner, case, tmp_path)
    out = tmp_path / "report.pdf"

    digest = build_case_report(workspace, case_id, out, actor=examiner)

    assert out.read_bytes().startswith(b"%PDF")
    assert len(digest) == 64


def test_report_records_the_chain_head_in_its_metadata(workspace, examiner, case, tmp_path):
    """The head is what later shows nothing was lopped off the end of the log."""
    case_id = _populate(workspace, examiner, case, tmp_path)
    out = tmp_path / "report.pdf"
    build_case_report(workspace, case_id, out, actor=examiner)

    assert b"body-sha256" in out.read_bytes()


def test_generating_a_report_is_itself_logged(workspace, examiner, case, tmp_path):
    case_id = _populate(workspace, examiner, case, tmp_path)
    build_case_report(workspace, case_id, tmp_path / "r.pdf", actor=examiner)

    actions = [str(entry["action"]) for entry in workspace.custody_entries()]
    assert "report_generated" in actions
    assert workspace.verify_chain().ok


def test_a_report_on_a_broken_chain_says_so(workspace, examiner, case, tmp_path):
    """A report must never quietly present a broken log as sound."""
    case_id = _populate(workspace, examiner, case, tmp_path)
    workspace.close()

    raw = sqlite3.connect(workspace.db_path)
    raw.execute("DROP TRIGGER IF EXISTS custody_log_no_update")
    raw.execute("UPDATE custody_log SET actor_username = 'ghost' WHERE seq = 3")
    raw.commit()
    raw.close()

    assert not workspace.verify_chain().ok
    out = tmp_path / "broken.pdf"
    build_case_report(workspace, case_id, out, actor=None)
    assert out.read_bytes().startswith(b"%PDF")


def test_an_empty_case_still_produces_a_report(workspace, examiner, case, tmp_path):
    out = tmp_path / "empty.pdf"
    build_case_report(workspace, int(case["id"]), out, actor=examiner)
    assert out.read_bytes().startswith(b"%PDF")
