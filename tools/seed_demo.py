#!/usr/bin/env python3
"""Create a demo workspace with a plausible case in it.

Used for screenshots, for the Playwright smoke tests, and for anyone who clones
the repository and wants something to look at before they have evidence of
their own.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coc.service import Workspace  # noqa: E402

PHOTO_JPEG = bytes.fromhex("ffd8ffe000104a46494600010100000100010000") + bytes(range(256)) * 40
PDF_STUB = b"%PDF-1.7\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"


def seed(root: Path, *, force: bool = False) -> Workspace:
    if force and root.exists():
        shutil.rmtree(root)

    workspace = Workspace.initialize(root)
    scratch = root / ".demo-source"
    scratch.mkdir(parents=True, exist_ok=True)

    kim = workspace.create_user("kim", "open-sesame-2026", full_name="K. Alvarez", role="admin")
    raj = workspace.create_user("raj", "second-examiner", full_name="R. Mehta", actor=kim)

    case = workspace.open_case(
        "2026-014",
        "Laptop and handset seizure — Aldgate",
        kim,
        notes="Two devices seized on scene, bagged and sealed at 22:40 local.",
    )
    case_id = int(case["id"])

    items = [
        ("dell-xps-15.dd", b"\x00DISKIMAGE\xff" * 90_000, "Full disk image, Dell XPS 15", "Dell XPS 15, S/N 7HX2K9"),
        ("pixel-8.ufd", b"UFDR-EXTRACT" * 40_000, "Logical extraction, Pixel 8", "Pixel 8, IMEI 35xxxxxxx0912"),
        ("usb-key.img", b"\xaaUSB\x55" * 25_000, "8 GB USB key, bit-for-bit", "SanDisk Cruzer, no serial"),
    ]
    evidence_ids = []
    for filename, payload, description, device in items:
        path = scratch / filename
        path.write_bytes(payload)
        record = workspace.ingest_evidence(
            case_id, path, kim, description=description, source_device=device
        )
        evidence_ids.append(int(record["id"]))

    attachments = [
        ("scene-01.jpg", PHOTO_JPEG, "photo", "image/jpeg", "Laptop in situ on desk", evidence_ids[0]),
        ("scene-02.jpg", PHOTO_JPEG + b"\x00" * 512, "photo", "image/jpeg", "Handset beside charger", evidence_ids[1]),
        ("warrant-2026-SW-88.pdf", PDF_STUB, "document", "application/pdf", "Search warrant", None),
        ("seizure-log.txt", b"22:40 devices bagged\n22:52 sealed, tag 4471\n", "document", "text/plain",
         "Contemporaneous seizure notes", None),
        ("hashes-on-scene.txt", b"sha256 computed by write blocker, see notes\n", "raw", "text/plain",
         "Write-blocker output", evidence_ids[0]),
    ]
    for filename, payload, kind, mime, caption, evidence_id in attachments:
        path = scratch / filename
        path.write_bytes(payload)
        workspace.attach(
            case_id, path, kim, evidence_id=evidence_id, kind=kind, caption=caption, mime_type=mime
        )

    workspace.verify_evidence(evidence_ids[0], kim)
    workspace.record_access(evidence_ids[1], kim)
    workspace.transfer_custody(evidence_ids[1], kim, raj, reason="Handset analysis")
    workspace.accept_custody(evidence_ids[1], raj)

    second = workspace.open_case("2026-021", "Cloud account preservation", kim, notes="Preservation request served.")
    path = scratch / "mailbox.mbox"
    path.write_bytes(b"From nobody\nSubject: preserved\n\n" * 12_000)
    workspace.ingest_evidence(int(second["id"]), path, raj, description="Mailbox export", source_device="Provider export")

    shutil.rmtree(scratch, ignore_errors=True)
    return workspace


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="workspace-demo")
    parser.add_argument("--force", action="store_true", help="delete an existing workspace first")
    arguments = parser.parse_args()

    workspace = seed(Path(arguments.root), force=arguments.force)
    stats = workspace.statistics()
    print(f"demo workspace ready at {arguments.root}")
    print(f"  cases      {stats['cases']}")
    print(f"  evidence   {stats['evidence']}")
    print(f"  attachments{stats['attachments']:>3}")
    print(f"  log        {stats['custody_entries']} entries, chain {stats['chain']['status']}")
    print("  sign in as 'kim' with access key 'open-sesame-2026'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
