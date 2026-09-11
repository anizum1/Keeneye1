#!/usr/bin/env python3
"""Prove the tamper-evidence claim by actually tampering.

Two separate attacks, run against a throwaway workspace:

1. **Evidence tampering.** The stored file is modified on disk, behind the
   application's back, with the read-only bit removed first. Re-hashing catches
   it, because the recorded digest no longer matches the bytes.

2. **Log tampering.** A historical custody entry is rewritten using a raw
   SQLite connection, with the append-only triggers dropped first — exactly what
   somebody with the database file and a shell would do. Chain verification
   catches it and names the entry, because the entry's contents no longer hash
   to the value the log recorded.

Exits non-zero. Both attacks are *supposed* to be detected, so a run in which
nothing is caught is a failure of the tool, not a success.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coc import chain, db  # noqa: E402
from coc.service import Workspace  # noqa: E402

BOLD, RED, GREEN, DIM, OFF = "\033[1m", "\033[31;1m", "\033[32m", "\033[2m", "\033[0m"


def _plain() -> bool:
    return not sys.stdout.isatty() or os.environ.get("NO_COLOR")


def say(text: str = "", style: str = "") -> None:
    print(f"{'' if _plain() else style}{text}{'' if _plain() else OFF}")


def rule(title: str) -> None:
    say()
    say(f"── {title} " + "─" * max(0, 66 - len(title)), BOLD)


def build(root: Path) -> tuple[Workspace, int]:
    workspace = Workspace.initialize(root)
    kim = workspace.create_user("kim", "demo-access-key", full_name="K. Alvarez", role="admin")
    raj = workspace.create_user("raj", "second-key", full_name="R. Mehta", actor=kim)
    case = workspace.open_case("2026-014", "Laptop seizure — Aldgate", kim)
    case_id = int(case["id"])

    source = root / ".source"
    source.mkdir(exist_ok=True)
    image = source / "dell-xps-15.dd"
    image.write_bytes(b"\x00DISKIMAGE\xff" * 40_000)

    item = workspace.ingest_evidence(
        case_id, image, kim, description="Full disk image", source_device="Dell XPS 15 S/N 7HX2K"
    )
    photo = source / "scene-01.jpg"
    photo.write_bytes(b"\xff\xd8\xff\xe0 scene photograph")
    workspace.attach(case_id, photo, kim, evidence_id=int(item["id"]), kind="photo",
                     caption="Laptop in situ", mime_type="image/jpeg")

    workspace.verify_evidence(int(item["id"]), kim)
    workspace.transfer_custody(int(item["id"]), kim, raj, reason="Handing to analyst")
    workspace.accept_custody(int(item["id"]), raj)

    shutil.rmtree(source, ignore_errors=True)
    return workspace, int(item["id"])


def attack_the_evidence(workspace: Workspace, evidence_id: int) -> bool:
    """Modify the vaulted file directly and see whether re-hashing notices."""
    rule("ATTACK 1 — alter the evidence file on disk")

    item = workspace.get_evidence(evidence_id)
    recorded = str(item["sha256"])
    path = workspace.vault.absolute_path_for(recorded)

    say(f"  recorded at intake   {recorded}", DIM)
    say(f"  vault file           {path.name[:32]}…", DIM)
    say(f"  file mode            {oct(path.stat().st_mode & 0o777)} (read-only)", DIM)

    payload = bytearray(path.read_bytes())
    say()
    say("  Removing the read-only bit and flipping one byte in the middle…")
    os.chmod(path, 0o644)
    position = len(payload) // 2
    before = payload[position]
    payload[position] ^= 0x01
    path.write_bytes(bytes(payload))
    say(f"  byte {position:,}: 0x{before:02x} → 0x{payload[position]:02x}  "
        f"(1 bit changed in {len(payload):,} bytes)", DIM)

    say()
    say("  Re-verifying…")
    examiner = workspace.find_user("kim")
    result = workspace.verify_evidence(evidence_id, examiner)

    if result.passed:
        say("  NOT DETECTED — the modified file still verified. This is a bug.", RED)
        return False

    say(f"  DETECTED — {result.item_number} failed verification", GREEN)
    say(f"    recorded  {result.original_sha256}")
    say(f"    observed  {result.observed_sha256}")
    say(f"    reason    {result.reason}")

    entry = workspace.custody_entries(evidence_id=evidence_id)[-1]
    say()
    say(f"  The failure was written to the log as entry #{entry['seq']} "
        f"(check result: {str(entry['hash_check_result']).upper()}).", DIM)
    say("  A tool that only records its successes would have lost this.", DIM)
    return True


def attack_the_log(workspace: Workspace) -> bool:
    """Rewrite a historical log entry with raw SQL and see whether the chain notices."""
    rule("ATTACK 2 — rewrite a custody entry in the database")

    before = workspace.verify_chain()
    say(f"  chain before: {before.status}, {before.entries_checked} entries", DIM)
    say(f"  head          {before.head_hash}", DIM)

    say()
    say("  The application cannot do this — the triggers refuse:")
    try:
        workspace.connection.execute("UPDATE custody_log SET action = 'nothing to see' WHERE seq = 3")
        say("  NOT BLOCKED — the append-only trigger did not fire. This is a bug.", RED)
        return False
    except sqlite3.IntegrityError as error:
        say(f"    sqlite3.IntegrityError: {error}", GREEN)

    say()
    say("  So do it the way an attacker would: raw connection, drop the triggers.")
    db_path = workspace.db_path
    workspace.close()

    raw = sqlite3.connect(db_path)
    target = raw.execute(
        "SELECT seq, action FROM custody_log WHERE action = 'custody_transferred' LIMIT 1"
    ).fetchone()
    seq = int(target[0]) if target else 3
    raw.execute("DROP TRIGGER IF EXISTS custody_log_no_update")
    raw.execute("DROP TRIGGER IF EXISTS custody_log_no_delete")
    raw.execute("UPDATE custody_log SET actor_username = 'someone_else' WHERE seq = ?", (seq,))
    raw.commit()
    raw.close()
    say(f"    entry #{seq}: actor_username rewritten to 'someone_else'", DIM)
    say("    the row itself now looks perfectly ordinary", DIM)

    say()
    say("  Verifying the chain…")
    after = chain.verify(db.connect(db_path))

    if after.ok:
        say("  NOT DETECTED — the chain still verified. This is a bug.", RED)
        return False

    say(f"  DETECTED — chain {after.status}", GREEN)
    say(f"    first broken entry  #{after.first_broken_seq}")
    say(f"    reason              {after.reason}")
    say()
    say("  The edit left no trace in the row. It left one in the arithmetic: the", DIM)
    say("  entry no longer hashes to the value the log recorded for it.", DIM)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--keep", metavar="DIR", help="build the workspace here and leave it behind")
    arguments = parser.parse_args()

    root = Path(arguments.keep) if arguments.keep else Path(tempfile.mkdtemp()) / "tamper-demo"
    if root.exists() and arguments.keep:
        shutil.rmtree(root)

    say()
    say("  KEENEYE — tamper-evidence demonstration", BOLD)
    say(f"  workspace: {root}", DIM)

    workspace, evidence_id = build(root)
    stats = workspace.statistics()
    say(f"  seeded 1 case, {stats['evidence']} evidence item, "
        f"{stats['attachments']} attachment, {stats['custody_entries']} log entries", DIM)

    caught_file = attack_the_evidence(workspace, evidence_id)
    caught_log = attack_the_log(workspace)

    rule("RESULT")
    say(f"  evidence tampering  {'DETECTED' if caught_file else 'MISSED'}",
        GREEN if caught_file else RED)
    say(f"  log tampering       {'DETECTED' if caught_log else 'MISSED'}",
        GREEN if caught_log else RED)
    say()

    if caught_file and caught_log:
        say("  Both attacks were caught. That is the whole claim:", BOLD)
        say("  the tool cannot prevent tampering, but it cannot be tampered with quietly.")
        say()
        if not arguments.keep:
            shutil.rmtree(root.parent, ignore_errors=True)
        return 0

    say("  An attack went undetected. The tool is not doing its job.", RED)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
