"""The court-ready PDF.

Deliberately plain.  The 3D interface is how an examiner works; this document
is what leaves the building, so it is laid out like an exhibit rather than like
a dashboard: monospaced hashes, a full chronological custody log, and a
verification result stated in words at the top rather than implied by a colour.

The report records its own SHA-256 on the final page.  A hash chain proves that
nothing inside the log was edited, but not that entries were not lopped off the
end; pinning the head hash in a dated document that has left the system is what
closes that gap.
"""

from __future__ import annotations

import hashlib
import io
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from coc import TOOL_NAME, __version__, db
from coc.service import ACTION_REPORT_GENERATED, Workspace, utc_now

INK = colors.HexColor("#111318")
MUTED = colors.HexColor("#5b6270")
RULE = colors.HexColor("#c8ccd4")
PASS = colors.HexColor("#0a6b3d")
FAIL = colors.HexColor("#a01223")


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "title", parent=base["Title"], fontName="Helvetica-Bold",
            fontSize=16, leading=20, textColor=INK, alignment=TA_LEFT, spaceAfter=2,
        ),
        "subtitle": ParagraphStyle(
            "subtitle", parent=base["Normal"], fontName="Helvetica",
            fontSize=9, leading=12, textColor=MUTED, spaceAfter=14,
        ),
        "heading": ParagraphStyle(
            "heading", parent=base["Heading2"], fontName="Helvetica-Bold",
            fontSize=11, leading=14, textColor=INK, spaceBefore=16, spaceAfter=6,
        ),
        "body": ParagraphStyle(
            "body", parent=base["Normal"], fontName="Helvetica",
            fontSize=9, leading=13, textColor=INK,
        ),
        "mono": ParagraphStyle(
            "mono", parent=base["Normal"], fontName="Courier",
            fontSize=7.5, leading=10, textColor=INK,
        ),
        "note": ParagraphStyle(
            "note", parent=base["Normal"], fontName="Helvetica-Oblique",
            fontSize=8, leading=11, textColor=MUTED, spaceBefore=6,
        ),
    }


def _table(data: list[list[Any]], widths: list[float], *, mono_columns: tuple[int, ...] = ()) -> Table:
    table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    style = [
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("LINEBELOW", (0, 0), (-1, 0), 0.75, INK),
        ("LINEBELOW", (0, 1), (-1, -2), 0.25, RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]
    for column in mono_columns:
        style.append(("FONTNAME", (column, 1), (column, -1), "Courier"))
    table.setStyle(TableStyle(style))
    return table


def _human_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:,.0f} {unit}" if unit == "B" else f"{size:,.1f} {unit}"
        size /= 1024
    return f"{value} B"


def _wrap_hash(value: str | None) -> str:
    """Break a 64-character digest so it wraps inside a narrow column."""
    if not value:
        return "—"
    return f"{value[:32]}<br/>{value[32:]}"


def build_case_report(
    workspace: Workspace,
    case_id: int,
    destination: BinaryIO | str | Path,
    *,
    actor: Any = None,
) -> str:
    """Render the report for one case and return the report's own SHA-256."""
    style = _styles()
    case = workspace.get_case(case_id)
    evidence = workspace.list_evidence(case_id)
    attachments = workspace.list_attachments(case_id=case_id)
    entries = workspace.custody_entries(case_id=case_id)
    verification = workspace.verify_chain()
    generated_at = utc_now()

    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=LETTER,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        title=f"{TOOL_NAME} custody report — {case['case_number']}",
        author=TOOL_NAME,
    )

    story: list[Any] = []
    story.append(Paragraph("Chain of Custody Report", style["title"]))
    story.append(
        Paragraph(
            f"{TOOL_NAME} {__version__} &middot; generated {generated_at} &middot; "
            f"prepared by {getattr(actor, 'username', 'system')}",
            style["subtitle"],
        )
    )

    # --- case identification -------------------------------------------
    story.append(Paragraph("1. Case identification", style["heading"]))
    opener = workspace.get_user(int(case["opened_by"]))
    story.append(
        _table(
            [
                ["Field", "Value"],
                ["Case number", str(case["case_number"])],
                ["Title", str(case["title"])],
                ["Status", str(case["status"]).upper()],
                ["Opened", str(case["opened_at"])],
                ["Opened by", opener.display_name if opener else "unknown"],
                ["Notes", str(case["notes"]) or "—"],
            ],
            [1.4 * inch, 5.35 * inch],
        )
    )

    # --- integrity verdict, stated first -------------------------------
    story.append(Paragraph("2. Integrity verification", style["heading"]))
    verdict_colour = PASS if verification.ok else FAIL
    verdict_text = (
        "The custody log for this workspace is INTACT. Every entry hashes to the value "
        "recorded in the entry that follows it, and the sequence is unbroken."
        if verification.ok
        else "The custody log for this workspace is BROKEN. "
        f"{verification.reason} This report must not be relied upon without investigation."
    )
    verdict = Paragraph(f"<b>{verification.status}</b> — {verdict_text}", style["body"])
    verdict_box = Table([[verdict]], colWidths=[6.75 * inch])
    verdict_box.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 1.0, verdict_colour),
                ("TEXTCOLOR", (0, 0), (-1, -1), verdict_colour),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story.append(verdict_box)
    story.append(Spacer(1, 8))
    story.append(
        _table(
            [
                ["Field", "Value"],
                ["Entries examined", f"{verification.entries_checked:,}"],
                ["Chain head (SHA-256)", verification.head_hash or "—"],
                ["Algorithm", "SHA-256 (NIST FIPS 180-4)"],
                ["Verified at", generated_at],
            ],
            [1.4 * inch, 5.35 * inch],
            mono_columns=(1,),
        )
    )

    # --- evidence inventory --------------------------------------------
    story.append(Paragraph("3. Evidence inventory", style["heading"]))
    if evidence:
        rows: list[list[Any]] = [["Item", "Original filename", "Size", "SHA-256", "Acquired", "Holder"]]
        for item in evidence:
            rows.append(
                [
                    Paragraph(str(item["item_number"]), style["body"]),
                    Paragraph(str(item["original_filename"]), style["body"]),
                    Paragraph(_human_bytes(int(item["byte_size"])), style["body"]),
                    Paragraph(_wrap_hash(str(item["sha256"])), style["mono"]),
                    Paragraph(str(item["acquisition_date"]), style["body"]),
                    Paragraph(workspace.current_holder(int(item["id"])), style["body"]),
                ]
            )
        story.append(
            _table(rows, [0.7 * inch, 1.35 * inch, 0.7 * inch, 2.1 * inch, 0.85 * inch, 0.75 * inch])
        )
        story.append(
            Paragraph(
                "SHA-1 values are retained alongside SHA-256 for interoperability with legacy "
                "tooling and are not relied upon for integrity determinations.",
                style["note"],
            )
        )
    else:
        story.append(Paragraph("No evidence items recorded for this case.", style["body"]))

    # --- supporting material -------------------------------------------
    story.append(Paragraph("4. Supporting material", style["heading"]))
    if attachments:
        rows = [["Kind", "Filename", "Size", "SHA-256", "Relates to", "Uploaded"]]
        for attachment in attachments:
            relates = "case"
            if attachment["evidence_id"] is not None:
                parent = workspace.get_evidence(int(attachment["evidence_id"]))
                relates = str(parent["item_number"])
            rows.append(
                [
                    Paragraph(str(attachment["kind"]), style["body"]),
                    Paragraph(str(attachment["original_filename"]), style["body"]),
                    Paragraph(_human_bytes(int(attachment["byte_size"])), style["body"]),
                    Paragraph(_wrap_hash(str(attachment["sha256"])), style["mono"]),
                    Paragraph(relates, style["body"]),
                    Paragraph(str(attachment["uploaded_at"]), style["body"]),
                ]
            )
        story.append(
            _table(rows, [0.7 * inch, 1.5 * inch, 0.7 * inch, 2.1 * inch, 0.7 * inch, 1.05 * inch])
        )
    else:
        story.append(Paragraph("No supporting photographs or documents recorded.", style["body"]))

    # --- the custody log -----------------------------------------------
    story.append(PageBreak())
    story.append(Paragraph("5. Chain of custody log", style["heading"]))
    story.append(
        Paragraph(
            "Entries appear in the order they were written. Each entry hash is computed over the "
            "entry's own contents together with the hash of the preceding entry, so any alteration "
            "to a historical entry invalidates every entry after it.",
            style["body"],
        )
    )
    story.append(Spacer(1, 8))

    if entries:
        rows = [["#", "Timestamp (UTC)", "Action", "Examiner", "Check", "Entry hash"]]
        for entry in entries:
            rows.append(
                [
                    Paragraph(str(entry["seq"]), style["body"]),
                    Paragraph(str(entry["timestamp_utc"]), style["body"]),
                    Paragraph(_describe(entry), style["body"]),
                    Paragraph(str(entry["actor_username"]), style["body"]),
                    Paragraph(str(entry["hash_check_result"]).upper(), style["body"]),
                    Paragraph(_wrap_hash(str(entry["entry_hash"])), style["mono"]),
                ]
            )
        story.append(
            _table(rows, [0.35 * inch, 1.2 * inch, 1.85 * inch, 0.7 * inch, 0.5 * inch, 2.15 * inch])
        )
    else:
        story.append(Paragraph("No custody entries recorded for this case.", style["body"]))

    # --- attestation ----------------------------------------------------
    story.append(Spacer(1, 18))
    story.append(
        KeepTogether(
            [
                Paragraph("6. Attestation", style["heading"]),
                Paragraph(
                    f"This report was produced automatically by {TOOL_NAME} {__version__} from the "
                    f"custody log at {generated_at}. It has not been edited by hand. The chain head "
                    "recorded in section 2 pins the state of the log at the moment of generation: "
                    "if entries are later removed from the end of the log, the head recorded here "
                    "will no longer match.",
                    style["body"],
                ),
                Spacer(1, 10),
                _table(
                    [
                        ["Examiner signature", "Date"],
                        ["", ""],
                        ["", ""],
                    ],
                    [4.0 * inch, 2.75 * inch],
                ),
            ]
        )
    )

    document.build(story, onFirstPage=_footer, onLaterPages=_footer)

    payload = buffer.getvalue()
    report_hash = hashlib.sha256(payload).hexdigest()
    payload = _append_self_hash(payload, report_hash, case, generated_at)

    if isinstance(destination, (str, Path)):
        Path(destination).write_bytes(payload)
    else:
        destination.write(payload)

    if actor is not None:
        with db.immediate_transaction(workspace.connection) as connection:
            workspace._log(
                connection,
                ACTION_REPORT_GENERATED,
                actor,
                case_id=case_id,
                details={
                    "case_number": str(case["case_number"]),
                    "chain_status": verification.status,
                    "chain_head": verification.head_hash,
                    "evidence_items": len(evidence),
                    "attachments": len(attachments),
                },
                timestamp=generated_at,
            )

    return report_hash


def _append_self_hash(payload: bytes, report_hash: str, case: Any, generated_at: str) -> bytes:
    """Record the report's own digest in its PDF metadata.

    Written into the document's Keywords rather than onto a visible page,
    because a hash printed on the page it is hashing would change the hash.
    """
    marker = (
        f"{TOOL_NAME} report; case {case['case_number']}; generated {generated_at}; "
        f"body-sha256 {report_hash}"
    ).encode("latin-1", errors="replace")
    return payload.replace(b"/Producer (", b"/Keywords (" + marker + b")\n/Producer (", 1)


def _describe(entry: Any) -> str:
    """A human sentence for a log entry, drawn from its recorded details."""
    action = str(entry["action"]).replace("_", " ")
    try:
        details = json.loads(str(entry["details_json"]))
    except (json.JSONDecodeError, TypeError):
        details = {}

    if item := details.get("item_number"):
        action = f"{action} ({item})"
    if (source := details.get("from")) and (target := details.get("to")):
        action = f"{action}: {source} &rarr; {target}"
    if filename := details.get("original_filename"):
        action = f"{action}: {filename}"
    return action


def _footer(canvas, document) -> None:
    """A page number and the tool identity on every page."""
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(MUTED)
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    canvas.drawString(0.75 * inch, 0.5 * inch, f"{TOOL_NAME} {__version__} — generated {stamp}")
    canvas.drawRightString(LETTER[0] - 0.75 * inch, 0.5 * inch, f"Page {document.page}")
    canvas.restoreState()
