"""The shell page and file delivery.

Only two kinds of response here: the single HTML document that boots the 3D
world, and bytes read back out of the vault.  Everything else is JSON, in
:mod:`coc.web.api`.
"""

from __future__ import annotations

import io
from pathlib import Path

from flask import Blueprint, Response, current_app, g, render_template, request, send_file

from coc import TOOL_NAME, __version__
from coc.service import Workspace
from coc.web.security import (
    INLINE_IMAGE_TYPES,
    current_examiner,
    harden_uploaded_response,
    login_required,
)

views = Blueprint("views", __name__)


def workspace() -> Workspace:
    return current_app.extensions["keeneye"]


@views.get("/")
def shell() -> str:
    """The only page.  Everything after this is the camera moving."""
    return render_template(
        "app.html",
        tool=TOOL_NAME,
        version=__version__,
        nonce=getattr(g, "csp_nonce", ""),
    )


@views.get("/healthz")
def healthz():
    chain = workspace().verify_chain()
    return {"status": "ok", "chain": chain.status, "entries": chain.entries_checked}


@views.get("/files/evidence/<int:evidence_id>")
@login_required
def download_evidence(evidence_id: int) -> Response:
    """Hand back an evidence file, and log that it was taken.

    Always a download, never rendered.  Evidence is by definition
    attacker-supplied content and has no business executing in the examiner's
    browser on the same origin as their session.
    """
    space = workspace()
    item = space.get_evidence(evidence_id)
    space.record_access(evidence_id, current_examiner(), downloaded=True)

    path = space.vault.absolute_path_for(str(item["sha256"]))
    if not path.is_file():
        return _vault_gap(str(item["item_number"]), str(item["sha256"]))

    response = send_file(
        path,
        as_attachment=True,
        download_name=str(item["original_filename"]),
        mimetype="application/octet-stream",
        conditional=True,
    )
    return harden_uploaded_response(response, inline=False)


@views.get("/files/attachment/<int:attachment_id>")
@login_required
def download_attachment(attachment_id: int) -> Response:
    """Serve a supporting file.

    Raster images may render inline so the gallery works; everything else —
    SVG included, because SVG is a scriptable document — is forced to download.
    """
    space = workspace()
    attachment = space.get_attachment(attachment_id)
    mime_type = str(attachment["mime_type"])
    inline = request.args.get("inline") == "1" and mime_type in INLINE_IMAGE_TYPES

    path = space.vault.absolute_path_for(str(attachment["sha256"]))
    if not path.is_file():
        return _vault_gap(str(attachment["original_filename"]), str(attachment["sha256"]))

    response = send_file(
        path,
        as_attachment=not inline,
        download_name=str(attachment["original_filename"]),
        mimetype=mime_type if inline else "application/octet-stream",
        conditional=True,
    )
    return harden_uploaded_response(response, inline=inline)


@views.get("/files/case/<int:case_id>/report.pdf")
@login_required
def case_report(case_id: int) -> Response:
    """Generate and return the court report for a case."""
    from coc.report import build_case_report

    space = workspace()
    case = space.get_case(case_id)
    buffer = io.BytesIO()
    build_case_report(space, case_id, buffer, actor=current_examiner())
    buffer.seek(0)

    filename = f"keeneye-{str(case['case_number']).replace('/', '-')}-report.pdf"
    response = send_file(
        buffer, as_attachment=True, download_name=filename, mimetype="application/pdf"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _vault_gap(label: str, sha256: str):
    """A stored file is missing from the vault.

    Reported as 410 Gone rather than 404: the record exists and says the bytes
    should be here, so their absence is a finding about the vault, not a typo
    in the URL.
    """
    return (
        {
            "error": "vault_gap",
            "message": (
                f"{label} is recorded with SHA-256 {sha256} but no longer present in the vault. "
                "Run a verification to record this in the custody log."
            ),
        },
        410,
    )


@views.get("/favicon.ico")
def favicon() -> Response:
    static_root = Path(current_app.static_folder or "")
    candidate = static_root / "favicon.ico"
    if candidate.is_file():
        return send_file(candidate)
    return Response(status=204)
