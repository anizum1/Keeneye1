"""The command line interface, including its exit codes.

The exit codes are part of the contract: 2 specifically means an integrity
check failed, so a scheduled job can tell "the tool broke" apart from "the
evidence does not verify".
"""

from __future__ import annotations

import json
import os
import sqlite3

import pytest

from coc.cli import EXIT_ERROR, EXIT_INTEGRITY, EXIT_OK, main


@pytest.fixture()
def ws(tmp_path):
    """An initialised workspace path, with two examiners and a case."""
    root = str(tmp_path / "ws")
    assert main(["--workspace", root, "init"]) == EXIT_OK
    assert main(["--workspace", root, "user", "add", "--username", "kim",
                 "--role", "admin", "--access-key", "open-sesame"]) == EXIT_OK
    assert main(["--workspace", root, "user", "add", "--username", "raj",
                 "--access-key", "second-key", "--as", "kim"]) == EXIT_OK
    assert main(["--workspace", root, "case", "open", "2026-014", "Laptop seizure",
                 "--as", "kim"]) == EXIT_OK
    return root


@pytest.fixture()
def evidence(tmp_path):
    path = tmp_path / "disk.dd"
    path.write_bytes(b"\x00IMAGE\xff" * 5000)
    return str(path)


def run(*args) -> int:
    return main(list(args))


def test_global_flags_work_after_the_subcommand(ws, evidence):
    """"evidence add file --as kim" is the natural order and must be accepted."""
    assert run("--workspace", ws, "evidence", "add", evidence,
               "--case", "2026-014", "--as", "kim") == EXIT_OK


def test_a_write_without_an_actor_is_refused(ws, evidence):
    """Nothing may be written to the log without a name attached to it."""
    assert run("--workspace", ws, "evidence", "add", evidence, "--case", "2026-014") == EXIT_ERROR


def test_an_unknown_actor_is_refused(ws, evidence):
    assert run("--workspace", ws, "evidence", "add", evidence,
               "--case", "2026-014", "--as", "ghost") == EXIT_ERROR


def test_commands_against_a_missing_workspace_fail_cleanly(tmp_path):
    assert run("--workspace", str(tmp_path / "nope"), "status") == EXIT_ERROR


def test_full_intake_verify_and_report_cycle(ws, evidence, tmp_path, capsys):
    assert run("--workspace", ws, "evidence", "add", evidence, "--case", "2026-014",
               "--as", "kim", "--source-device", "Dell XPS") == EXIT_OK

    photo = tmp_path / "scene.jpg"
    photo.write_bytes(b"\xff\xd8 photo")
    assert run("--workspace", ws, "attach", str(photo), "--case", "2026-014",
               "--evidence", "1", "--kind", "photo", "--as", "kim") == EXIT_OK

    assert run("--workspace", ws, "verify", "--case", "2026-014", "--as", "kim") == EXIT_OK
    assert run("--workspace", ws, "chain", "verify") == EXIT_OK

    report = tmp_path / "report.pdf"
    assert run("--workspace", ws, "report", "2026-014", "--out", str(report), "--as", "kim") == EXIT_OK
    assert report.read_bytes().startswith(b"%PDF")


def test_verify_exits_with_the_integrity_code_when_a_file_was_altered(ws, evidence):
    run("--workspace", ws, "evidence", "add", evidence, "--case", "2026-014", "--as", "kim")

    from coc.service import Workspace

    workspace = Workspace(ws)
    stored = workspace.vault.absolute_path_for(str(workspace.get_evidence(1)["sha256"]))
    os.chmod(stored, 0o644)
    stored.write_bytes(b"tampered")

    assert run("--workspace", ws, "verify", "--evidence", "1", "--as", "kim") == EXIT_INTEGRITY


def test_chain_verify_exits_with_the_integrity_code_when_the_log_was_edited(ws, evidence):
    run("--workspace", ws, "evidence", "add", evidence, "--case", "2026-014", "--as", "kim")

    raw = sqlite3.connect(f"{ws}/keeneye.sqlite")
    raw.execute("DROP TRIGGER IF EXISTS custody_log_no_update")
    raw.execute("UPDATE custody_log SET actor_username = 'someone_else' WHERE seq = 3")
    raw.commit()
    raw.close()

    assert run("--workspace", ws, "chain", "verify") == EXIT_INTEGRITY
    assert run("--workspace", ws, "status") == EXIT_INTEGRITY


def test_transfer_then_accept(ws, evidence):
    run("--workspace", ws, "evidence", "add", evidence, "--case", "2026-014", "--as", "kim")
    assert run("--workspace", ws, "transfer", "1", "--to", "raj", "--as", "kim") == EXIT_OK
    assert run("--workspace", ws, "accept", "1", "--as", "raj") == EXIT_OK
    # Only the named recipient may accept.
    assert run("--workspace", ws, "accept", "1", "--as", "kim") == EXIT_ERROR


def test_json_mode_emits_parsable_output(ws, capsys):
    capsys.readouterr()  # discard the prose the fixture's setup commands printed
    assert run("--workspace", ws, "--json", "chain", "verify") == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "INTACT"
    assert isinstance(payload["entries_checked"], int)


def test_json_mode_suppresses_the_prose(ws, capsys):
    capsys.readouterr()
    run("--workspace", ws, "--json", "status")
    output = capsys.readouterr().out
    assert "KEENEYE 0.1.0 —" not in output
    json.loads(output)


def test_cli_and_web_share_one_log(ws, evidence, tmp_path):
    """An action taken in the terminal has to show up in the web interface."""
    from coc.web.app import create_app

    run("--workspace", ws, "evidence", "add", evidence, "--case", "2026-014", "--as", "kim")

    app = create_app(ws, secret_key="test", testing=True)
    client = app.test_client()
    token = client.get("/api/session").get_json()["csrf_token"]
    client.post("/api/session", json={"username": "kim", "access_key": "open-sesame"},
                headers={"X-CSRF-Token": token})

    actions = [entry["action"] for entry in client.get("/api/chain").get_json()["entries"]]
    assert "evidence_ingested" in actions
