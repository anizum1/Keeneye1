"""The JSON API the 3D shell runs on.

Every endpoint that changes anything goes through :class:`coc.service.Workspace`,
so a browser client has no way to reach state that was not also written to the
custody log.
"""

from __future__ import annotations

import json
from typing import Any

from flask import Blueprint, current_app, jsonify, request

from coc import TOOL_NAME, __version__
from coc.metadata import from_browser
from coc.service import ServiceError, Workspace, rows_to_dicts
from coc.storage import StorageError
from coc.web.security import (
    admin_required,
    csrf_token,
    current_examiner,
    login_required,
    sign_in,
    sign_out,
)

api = Blueprint("api", __name__, url_prefix="/api")


def workspace() -> Workspace:
    return current_app.extensions["keeneye"]


@api.errorhandler(ServiceError)
def _service_error(error: ServiceError):
    return jsonify({"error": "refused", "message": str(error)}), 400


@api.errorhandler(StorageError)
def _storage_error(error: StorageError):
    return jsonify({"error": "storage", "message": str(error)}), 400


def _payload() -> dict[str, Any]:
    return request.get_json(silent=True) or {}


# ----------------------------------------------------------------------
# session
# ----------------------------------------------------------------------


@api.get("/session")
def read_session():
    """What the shell asks on boot to decide which scene to open in."""
    examiner = current_examiner()
    return jsonify(
        {
            "authenticated": examiner is not None,
            "examiner": examiner.as_dict() if examiner else None,
            "csrf_token": csrf_token(),
            "tool": {"name": TOOL_NAME, "version": __version__},
        }
    )


@api.post("/session")
def create_session():
    """Sign in.  The 'access key' is the examiner's credential."""
    data = _payload()
    username = str(data.get("username", "")).strip()
    access_key = str(data.get("access_key") or data.get("password") or "")

    if not username or not access_key:
        return jsonify({"error": "invalid", "message": "Username and access key are required."}), 400

    examiner = workspace().authenticate(username, access_key)
    if examiner is None:
        # Deliberately vague: which half was wrong is not the caller's business.
        return jsonify({"error": "rejected", "message": "Key rejected."}), 401

    sign_in(examiner)
    return jsonify({"authenticated": True, "examiner": examiner.as_dict(), "csrf_token": csrf_token()})


@api.delete("/session")
@login_required
def destroy_session():
    sign_out()
    return jsonify({"authenticated": False})


# ----------------------------------------------------------------------
# overview
# ----------------------------------------------------------------------


@api.get("/overview")
@login_required
def overview():
    space = workspace()
    return jsonify(
        {
            "statistics": space.statistics(),
            "cases": rows_to_dicts(space.list_cases()),
            "examiner": current_examiner().as_dict(),
        }
    )


# ----------------------------------------------------------------------
# cases
# ----------------------------------------------------------------------


@api.get("/cases")
@login_required
def list_cases():
    return jsonify({"cases": rows_to_dicts(workspace().list_cases())})


@api.post("/cases")
@login_required
def open_case():
    data = _payload()
    case = workspace().open_case(
        str(data.get("case_number", "")),
        str(data.get("title", "")),
        current_examiner(),
        notes=str(data.get("notes", "")),
    )
    return jsonify({"case": dict(case)}), 201


@api.get("/cases/<int:case_id>")
@login_required
def read_case(case_id: int):
    space = workspace()
    case = space.get_case(case_id)
    evidence = rows_to_dicts(space.list_evidence(case_id))
    for item in evidence:
        item["holder"] = space.current_holder(int(item["id"]))
        item["pending_transfer"] = space.pending_transfer(int(item["id"]))
    return jsonify(
        {
            "case": dict(case),
            "evidence": evidence,
            "attachments": rows_to_dicts(space.list_attachments(case_id=case_id)),
            "custody": _decode_entries(space.custody_entries(case_id=case_id)),
        }
    )


@api.get("/cases/<int:case_id>/graph")
@login_required
def case_graph(case_id: int):
    """Nodes and links for the 3D case web.

    Shaped for the renderer rather than for the database: one flat node list
    with a ``group`` the layout uses to decide which shell a node settles onto.
    """
    space = workspace()
    case = space.get_case(case_id)
    nodes: list[dict[str, Any]] = [
        {
            "id": f"case:{case_id}",
            "group": "case",
            "label": str(case["case_number"]),
            "detail": str(case["title"]),
            "status": str(case["status"]),
        }
    ]
    links: list[dict[str, str]] = []
    examiners: set[str] = set()

    for item in space.list_evidence(case_id):
        node_id = f"evidence:{item['id']}"
        nodes.append(
            {
                "id": node_id,
                "group": "evidence",
                "label": str(item["item_number"]),
                "detail": str(item["original_filename"]),
                "sha256": str(item["sha256"]),
                "byte_size": int(item["byte_size"]),
                "holder": space.current_holder(int(item["id"])),
            }
        )
        links.append({"source": f"case:{case_id}", "target": node_id, "kind": "contains"})

        holder = space.current_holder(int(item["id"]))
        if holder and holder != "unknown":
            examiners.add(holder)
            links.append({"source": f"examiner:{holder}", "target": node_id, "kind": "holds"})

    for attachment in space.list_attachments(case_id=case_id):
        node_id = f"attachment:{attachment['id']}"
        nodes.append(
            {
                "id": node_id,
                "group": "attachment",
                "kind": str(attachment["kind"]),
                "label": str(attachment["original_filename"]),
                "detail": str(attachment["caption"]),
                "sha256": str(attachment["sha256"]),
                "byte_size": int(attachment["byte_size"]),
            }
        )
        parent = (
            f"evidence:{attachment['evidence_id']}"
            if attachment["evidence_id"] is not None
            else f"case:{case_id}"
        )
        links.append({"source": parent, "target": node_id, "kind": "supports"})

    for username in sorted(examiners):
        nodes.append(
            {"id": f"examiner:{username}", "group": "examiner", "label": username, "detail": "examiner"}
        )

    return jsonify({"case": dict(case), "nodes": nodes, "links": links})


# ----------------------------------------------------------------------
# evidence and attachments
# ----------------------------------------------------------------------


@api.post("/cases/<int:case_id>/evidence")
@login_required
def ingest_evidence(case_id: int):
    """Admit an uploaded file as evidence, hashing it on the way in."""
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return jsonify({"error": "invalid", "message": "No file was uploaded."}), 400

    item = workspace().ingest_evidence_stream(
        case_id,
        upload.stream,
        current_examiner(),
        original_filename=upload.filename,
        total_size=request.content_length,
        description=request.form.get("description", ""),
        source_device=request.form.get("source_device", ""),
        acquisition_date=request.form.get("acquisition_date") or None,
        # An upload has no path to stat, so the original file's age would be
        # lost entirely. The browser reports it; recorded as the client's claim.
        timestamps=from_browser(request.form.get("last_modified")),
    )
    return jsonify({"evidence": dict(item)}), 201


@api.post("/cases/<int:case_id>/attachments")
@login_required
def upload_attachment(case_id: int):
    """Admit a supporting photo, document or raw file."""
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return jsonify({"error": "invalid", "message": "No file was uploaded."}), 400

    evidence_id = request.form.get("evidence_id")
    attachment = workspace().attach(
        case_id,
        upload.stream,
        current_examiner(),
        evidence_id=int(evidence_id) if evidence_id else None,
        kind=request.form.get("kind", "other"),
        caption=request.form.get("caption", ""),
        original_filename=upload.filename,
        mime_type=upload.mimetype or "application/octet-stream",
        total_size=request.content_length,
        timestamps=from_browser(request.form.get("last_modified")),
    )
    return jsonify({"attachment": dict(attachment)}), 201


@api.get("/evidence/<int:evidence_id>")
@login_required
def read_evidence(evidence_id: int):
    space = workspace()
    item = space.get_evidence(evidence_id)
    space.record_access(evidence_id, current_examiner())
    return jsonify(
        {
            "evidence": dict(item),
            "holder": space.current_holder(evidence_id),
            "pending_transfer": space.pending_transfer(evidence_id),
            "attachments": rows_to_dicts(space.list_attachments(evidence_id=evidence_id)),
            "custody": _decode_entries(space.custody_entries(evidence_id=evidence_id)),
        }
    )


@api.post("/evidence/<int:evidence_id>/verify")
@login_required
def verify_evidence(evidence_id: int):
    result = workspace().verify_evidence(evidence_id, current_examiner())
    return jsonify({"verification": result.as_dict()})


@api.post("/evidence/<int:evidence_id>/transfer")
@login_required
def transfer_custody(evidence_id: int):
    data = _payload()
    space = workspace()
    recipient = space.find_user(str(data.get("to", "")))
    if recipient is None:
        return jsonify({"error": "invalid", "message": "No such examiner."}), 400

    entry = space.transfer_custody(
        evidence_id, current_examiner(), recipient, reason=str(data.get("reason", ""))
    )
    return jsonify({"entry": _decode_entry(entry)}), 201


@api.post("/evidence/<int:evidence_id>/accept")
@login_required
def accept_custody(evidence_id: int):
    entry = workspace().accept_custody(evidence_id, current_examiner())
    return jsonify({"entry": _decode_entry(entry)}), 201


# ----------------------------------------------------------------------
# the chain
# ----------------------------------------------------------------------


@api.get("/chain")
@login_required
def read_chain():
    space = workspace()
    case_id = request.args.get("case_id", type=int)
    evidence_id = request.args.get("evidence_id", type=int)
    return jsonify(
        {
            "verification": space.verify_chain().as_dict(),
            "entries": _decode_entries(
                space.custody_entries(case_id=case_id, evidence_id=evidence_id)
            ),
        }
    )


@api.post("/chain/verify")
@login_required
def verify_chain():
    return jsonify({"verification": workspace().verify_chain().as_dict()})


# ----------------------------------------------------------------------
# users
# ----------------------------------------------------------------------


@api.patch("/users/<int:user_id>")
@admin_required
def update_user(user_id: int):
    """Activate or deactivate an examiner. Accounts are never deleted."""
    data = _payload()
    if "active" not in data:
        return jsonify({"error": "invalid", "message": "Nothing to change."}), 400

    examiner = workspace().set_user_active(user_id, bool(data["active"]), current_examiner())
    return jsonify({"user": examiner.as_dict(), "active": bool(data["active"])})


@api.get("/users")
@login_required
def list_users():
    return jsonify({"users": rows_to_dicts(workspace().list_users())})


@api.post("/users")
@admin_required
def create_user():
    data = _payload()
    examiner = workspace().create_user(
        str(data.get("username", "")),
        str(data.get("access_key") or data.get("password") or ""),
        full_name=str(data.get("full_name", "")),
        role=str(data.get("role", "examiner")),
        actor=current_examiner(),
    )
    return jsonify({"user": examiner.as_dict()}), 201


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------


def _decode_entry(entry: Any) -> dict[str, Any]:
    """Turn a stored log entry into JSON, expanding ``details_json``."""
    record = dict(entry)
    raw = record.pop("details_json", "{}")
    try:
        record["details"] = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except json.JSONDecodeError:
        record["details"] = {}
    return record


def _decode_entries(entries: Any) -> list[dict[str, Any]]:
    return [_decode_entry(entry) for entry in entries]
