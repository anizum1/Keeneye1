"""The HTTP surface: authentication, CSRF, uploads and how files are served.

The file-serving tests matter more than they look. This application hands back
bytes that somebody else uploaded, on the same origin as the examiner's
session, so the rules about what may render inline are a security boundary
rather than a presentation choice.
"""

from __future__ import annotations

import io

import pytest

from coc.web.app import create_app


@pytest.fixture()
def app(tmp_path):
    application = create_app(tmp_path / "ws", secret_key="test-secret", testing=True)
    workspace = application.extensions["keeneye"]
    kim = workspace.create_user("kim", "open-sesame", full_name="K. Alvarez", role="admin")
    workspace.create_user("raj", "second-key", full_name="R. Mehta", actor=kim)
    workspace.open_case("2026-014", "Laptop seizure", kim)
    return application


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def signed_in(client):
    """A client with a session and the CSRF token wired into its headers."""
    token = client.get("/api/session").get_json()["csrf_token"]
    response = client.post(
        "/api/session",
        json={"username": "kim", "access_key": "open-sesame"},
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 200
    client.environ_base["HTTP_X_CSRF_TOKEN"] = response.get_json()["csrf_token"]
    return client


def upload(client, path, filename, content, **fields):
    data = {"file": (io.BytesIO(content), filename)}
    data.update(fields)
    return client.post(path, data=data, content_type="multipart/form-data")


# ----------------------------------------------------------------------
# the shell and its headers
# ----------------------------------------------------------------------


def test_shell_is_served_with_a_strict_csp(client):
    response = client.get("/")
    assert response.status_code == 200
    policy = response.headers["Content-Security-Policy"]

    assert "script-src 'self' 'nonce-" in policy
    assert "unsafe-inline" not in policy
    assert "frame-ancestors 'none'" in policy
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_the_import_map_nonce_matches_the_response_policy(client):
    response = client.get("/")
    body = response.get_data(as_text=True)
    nonce = response.headers["Content-Security-Policy"].split("'nonce-")[1].split("'")[0]
    assert f'nonce="{nonce}"' in body


def test_healthz_reports_chain_state(client):
    payload = client.get("/healthz").get_json()
    assert payload["status"] == "ok"
    assert payload["chain"] == "INTACT"


# ----------------------------------------------------------------------
# authentication and CSRF
# ----------------------------------------------------------------------


def test_api_requires_a_session(client):
    assert client.get("/api/overview").status_code == 401
    assert client.get("/api/cases").status_code == 401


def test_sign_in_and_out(client):
    token = client.get("/api/session").get_json()["csrf_token"]
    response = client.post("/api/session", json={"username": "kim", "access_key": "open-sesame"},
                           headers={"X-CSRF-Token": token})
    assert response.status_code == 200
    assert response.get_json()["examiner"]["username"] == "kim"

    fresh = response.get_json()["csrf_token"]
    assert client.get("/api/overview").status_code == 200
    assert client.delete("/api/session", headers={"X-CSRF-Token": fresh}).status_code == 200
    assert client.get("/api/overview").status_code == 401


def test_wrong_key_is_rejected_without_saying_which_half_was_wrong(client):
    token = client.get("/api/session").get_json()["csrf_token"]
    response = client.post("/api/session", json={"username": "kim", "access_key": "wrong"},
                           headers={"X-CSRF-Token": token})
    assert response.status_code == 401
    message = response.get_json()["message"].lower()
    assert "key rejected" in message
    assert "username" not in message and "password" not in message


def test_state_changing_requests_need_the_csrf_token(signed_in):
    del signed_in.environ_base["HTTP_X_CSRF_TOKEN"]
    response = signed_in.post("/api/chain/verify")
    assert response.status_code == 403
    assert response.get_json()["error"] == "csrf_failed"


def test_a_forged_csrf_token_is_refused(signed_in):
    signed_in.environ_base["HTTP_X_CSRF_TOKEN"] = "not-the-right-token"
    assert signed_in.post("/api/chain/verify").status_code == 403


# ----------------------------------------------------------------------
# evidence and attachment upload
# ----------------------------------------------------------------------


def test_uploading_evidence_hashes_and_logs_it(signed_in, app):
    response = upload(signed_in, "/api/cases/1/evidence", "disk.dd", b"\x00IMAGE\xff" * 500,
                      description="Full disk image", source_device="Dell XPS")
    assert response.status_code == 201

    item = response.get_json()["evidence"]
    assert item["item_number"] == "ITEM-001"
    assert len(item["sha256"]) == 64

    actions = [e["action"] for e in signed_in.get("/api/chain").get_json()["entries"]]
    assert "evidence_ingested" in actions


def test_uploading_an_attachment_binds_it_to_the_item(signed_in):
    upload(signed_in, "/api/cases/1/evidence", "disk.dd", b"image bytes")
    response = upload(signed_in, "/api/cases/1/attachments", "scene.jpg", b"\xff\xd8 photo",
                      kind="photo", caption="Laptop in situ", evidence_id="1")
    assert response.status_code == 201

    attachment = response.get_json()["attachment"]
    assert attachment["kind"] == "photo"
    assert attachment["evidence_id"] == 1


def test_upload_without_a_file_is_refused(signed_in):
    response = signed_in.post("/api/cases/1/evidence", data={}, content_type="multipart/form-data")
    assert response.status_code == 400


def test_oversized_uploads_are_refused(signed_in, app):
    app.config["MAX_CONTENT_LENGTH"] = 1024
    response = upload(signed_in, "/api/cases/1/evidence", "big.dd", b"x" * 4096)
    assert response.status_code == 413


# ----------------------------------------------------------------------
# serving somebody else's bytes
# ----------------------------------------------------------------------


def test_evidence_always_downloads_never_renders(signed_in):
    upload(signed_in, "/api/cases/1/evidence", "page.html", b"<script>alert(1)</script>")
    response = signed_in.get("/files/evidence/1")

    assert response.headers["Content-Disposition"].startswith("attachment")
    assert response.headers["Content-Type"] == "application/octet-stream"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in response.headers["Content-Security-Policy"]


def test_a_photograph_may_render_inline(signed_in):
    upload(signed_in, "/api/cases/1/attachments", "scene.jpg", b"\xff\xd8 photo", kind="photo")
    # Flask derives the mimetype from the filename on upload.
    response = signed_in.get("/files/attachment/1?inline=1")
    assert response.headers["Content-Disposition"].startswith("inline")
    assert response.headers["Content-Type"].startswith("image/jpeg")


def test_an_svg_is_never_rendered_inline(signed_in):
    """SVG is a scriptable document, not an image format."""
    upload(signed_in, "/api/cases/1/attachments", "evil.svg",
           b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', kind="other")
    response = signed_in.get("/files/attachment/1?inline=1")

    assert response.headers["Content-Disposition"].startswith("attachment")
    assert response.headers["Content-Type"] == "application/octet-stream"


def test_downloading_evidence_is_logged(signed_in):
    upload(signed_in, "/api/cases/1/evidence", "disk.dd", b"bytes")
    signed_in.get("/files/evidence/1")

    actions = [e["action"] for e in signed_in.get("/api/chain").get_json()["entries"]]
    assert "evidence_downloaded" in actions


def test_a_missing_vault_file_reports_a_gap_not_a_crash(signed_in, app):
    import os

    upload(signed_in, "/api/cases/1/evidence", "disk.dd", b"bytes")
    workspace = app.extensions["keeneye"]
    path = workspace.vault.absolute_path_for(str(workspace.get_evidence(1)["sha256"]))
    os.chmod(path, 0o644)
    os.unlink(path)

    response = signed_in.get("/files/evidence/1")
    assert response.status_code == 410
    assert response.get_json()["error"] == "vault_gap"


# ----------------------------------------------------------------------
# the rest of the API
# ----------------------------------------------------------------------


def test_graph_shapes_nodes_for_the_renderer(signed_in):
    upload(signed_in, "/api/cases/1/evidence", "disk.dd", b"bytes")
    upload(signed_in, "/api/cases/1/attachments", "scene.jpg", b"\xff\xd8", kind="photo", evidence_id="1")

    payload = signed_in.get("/api/cases/1/graph").get_json()
    groups = {node["group"] for node in payload["nodes"]}
    assert {"case", "evidence", "attachment"} <= groups

    ids = {node["id"] for node in payload["nodes"]}
    for link in payload["links"]:
        assert link["source"] in ids and link["target"] in ids


def test_verification_endpoint_reports_a_pass(signed_in):
    upload(signed_in, "/api/cases/1/evidence", "disk.dd", b"bytes")
    payload = signed_in.post("/api/evidence/1/verify").get_json()
    assert payload["verification"]["passed"] is True


def test_transfer_and_accept_over_http(signed_in, client):
    upload(signed_in, "/api/cases/1/evidence", "disk.dd", b"bytes")
    assert signed_in.post("/api/evidence/1/transfer", json={"to": "raj"}).status_code == 201

    detail = signed_in.get("/api/cases/1").get_json()
    assert detail["evidence"][0]["pending_transfer"]["to"] == "raj"


def test_transfer_to_an_unknown_examiner_is_refused(signed_in):
    upload(signed_in, "/api/cases/1/evidence", "disk.dd", b"bytes")
    assert signed_in.post("/api/evidence/1/transfer", json={"to": "nobody"}).status_code == 400


def test_report_is_a_pdf_and_is_logged(signed_in):
    upload(signed_in, "/api/cases/1/evidence", "disk.dd", b"bytes")
    response = signed_in.get("/files/case/1/report.pdf")

    assert response.status_code == 200
    assert response.data.startswith(b"%PDF")
    assert response.headers["Content-Disposition"].startswith("attachment")

    actions = [e["action"] for e in signed_in.get("/api/chain").get_json()["entries"]]
    assert "report_generated" in actions


def test_creating_a_user_requires_admin(signed_in, app):
    assert signed_in.post("/api/users", json={"username": "new", "access_key": "k"}).status_code == 201

    token = signed_in.get("/api/session").get_json()["csrf_token"]
    signed_in.delete("/api/session", headers={"X-CSRF-Token": token})
    token = signed_in.get("/api/session").get_json()["csrf_token"]
    signed_in.post("/api/session", json={"username": "raj", "access_key": "second-key"},
                   headers={"X-CSRF-Token": token})
    signed_in.environ_base["HTTP_X_CSRF_TOKEN"] = signed_in.get("/api/session").get_json()["csrf_token"]

    response = signed_in.post("/api/users", json={"username": "another", "access_key": "k"})
    assert response.status_code == 403


def test_the_chain_stays_intact_across_the_whole_http_workflow(signed_in):
    upload(signed_in, "/api/cases/1/evidence", "disk.dd", b"bytes")
    upload(signed_in, "/api/cases/1/attachments", "scene.jpg", b"\xff\xd8", kind="photo", evidence_id="1")
    signed_in.post("/api/evidence/1/verify")
    signed_in.get("/files/evidence/1")
    signed_in.post("/api/evidence/1/transfer", json={"to": "raj"})
    signed_in.get("/files/case/1/report.pdf")

    verification = signed_in.post("/api/chain/verify").get_json()["verification"]
    assert verification["ok"] is True
