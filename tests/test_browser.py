"""End-to-end tests that drive the real 3D interface in Chromium.

These exist because the parts of this application most likely to break
silently are the ones no unit test can see: whether a WebGL context is
acquired at all, whether the login animation and the credential check stay in
step, and whether an upload genuinely locks into the vault rather than
appearing to.

Skipped automatically when Playwright or a browser binary is unavailable, so
the rest of the suite still runs anywhere.
"""

from __future__ import annotations

import os
import socket
import threading
from pathlib import Path

import pytest

playwright_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

CHROMIUM_CANDIDATES = (
    os.environ.get("KEENEYE_CHROMIUM"),
    "/opt/pw-browsers/chromium",
)

# SwiftShader: these runs happen on machines with no GPU.
LAUNCH_ARGS = ["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--no-sandbox"]

ACCESS_KEY = "open-sesame-2026"


def _chromium_path() -> str | None:
    for candidate in CHROMIUM_CANDIDATES:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    """A real Flask server on a real port; the test client cannot run WebGL."""
    from werkzeug.serving import make_server

    from coc.web.app import create_app

    root = tmp_path_factory.mktemp("browser-ws") / "ws"
    app = create_app(root, secret_key="browser-test-secret", testing=False)

    workspace = app.extensions["keeneye"]
    kim = workspace.create_user("kim", ACCESS_KEY, full_name="K. Alvarez", role="admin")
    workspace.create_user("raj", "second-key", full_name="R. Mehta", actor=kim)
    case = workspace.open_case("2026-014", "Laptop seizure — Aldgate", kim)

    seed = root.parent / "seed.dd"
    seed.write_bytes(b"\x00IMAGE\xff" * 5000)
    workspace.ingest_evidence(int(case["id"]), seed, kim, description="Disk image")

    port = _free_port()
    httpd = make_server("127.0.0.1", port, app, threaded=True)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        thread.join(timeout=5)


@pytest.fixture(scope="module")
def browser():
    """Launch Chromium, wherever it happens to live.

    On a CI runner `playwright install` puts it where Playwright expects, so no
    path is needed. In a sandbox with a pre-installed browser whose build number
    does not match the installed Playwright, the explicit path is the only way in.
    Try both before giving up.
    """
    with playwright_api.sync_playwright() as playwright:
        attempts = [None, _chromium_path()]
        failures = []
        for executable in attempts:
            if executable is None and attempts.index(executable) > 0:
                continue
            try:
                instance = playwright.chromium.launch(
                    executable_path=executable, args=LAUNCH_ARGS
                )
            except Exception as error:  # noqa: BLE001 - reported below if all attempts fail
                failures.append(f"{executable or 'default'}: {error}")
                continue
            yield instance
            instance.close()
            return
        pytest.skip("no usable Chromium: " + "; ".join(failures))


@pytest.fixture()
def page(browser):
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    page = context.new_page()
    page.errors = []
    page.on("pageerror", lambda error: page.errors.append(str(error)))
    page.on("console", lambda message: page.errors.append(message.text)
            if message.type == "error" else None)
    yield page
    context.close()


def _boot(page, server, query: str = "") -> None:
    page.goto(f"{server}/{query}", wait_until="networkidle")
    page.wait_for_function("() => document.body.dataset.ready === '1'", timeout=45_000)


def _sign_in(page, server) -> None:
    _boot(page, server)
    page.fill("#gate-user", "kim")
    page.fill("#gate-key", ACCESS_KEY)
    page.click("#gate-turn")
    page.wait_for_selector("#ui:not([hidden])", timeout=45_000)


# ----------------------------------------------------------------------


def test_the_world_boots_with_a_webgl_context_and_no_errors(page, server):
    _boot(page, server)

    assert page.evaluate("!!keeneye.world.renderer.getContext()")
    assert page.is_visible("#gate")
    assert page.evaluate("keeneye.world.composer.passes.length") >= 3
    assert page.errors == []


def test_the_key_is_recut_from_a_hash_of_what_is_typed(page, server):
    """The cut has to follow the credential, not just look like it does."""
    _boot(page, server)

    page.fill("#gate-key", "first-credential")
    page.wait_for_timeout(500)
    first = page.evaluate("keeneye.scenes.gate.currentDigest")

    page.fill("#gate-key", "second-credential")
    page.wait_for_timeout(500)
    second = page.evaluate("keeneye.scenes.gate.currentDigest")

    assert len(first) == 64 and len(second) == 64
    assert first != second

    # And it is genuinely SHA-256 of the input, not a decorative stand-in.
    import hashlib

    assert second == hashlib.sha256(b"second-credential").hexdigest()


def test_a_rejected_key_does_not_open_the_lock(page, server):
    _boot(page, server)
    page.fill("#gate-user", "kim")
    page.fill("#gate-key", "wrong-key")
    page.click("#gate-turn")

    page.wait_for_selector("#gate-error:not([hidden])", timeout=30_000)
    assert page.is_visible("#gate")
    assert page.is_hidden("#ui")
    assert page.evaluate("keeneye.scenes.gate.state") in {"rejected", "sealed"}


def test_signing_in_opens_the_vault(page, server):
    _sign_in(page, server)

    assert "K. Alvarez" in page.inner_text("#whoami")
    assert "CHAIN INTACT" in page.inner_text("#chainstatus").upper()
    assert page.errors == []


def test_navigating_flies_the_camera_and_shows_only_that_region(page, server):
    _sign_in(page, server)

    for region, heading in [("forge", "Intake"), ("web", "2026-014"), ("ledger", "Ledger")]:
        page.click(f"[data-goto='{region}']")
        page.wait_for_function(
            f"() => keeneye.world.current === '{region}' && !keeneye.world.flight",
            timeout=30_000,
        )
        assert heading in page.inner_text(".panel-head")

        visible = page.evaluate(
            "Object.fromEntries([...keeneye.world.regions].map(([n,r]) => [n, r.group.visible]))"
        )
        assert [name for name, shown in visible.items() if shown] == [region]

    assert page.errors == []


def test_uploading_a_file_hashes_it_and_locks_it_into_the_vault(page, server, tmp_path):
    """The scene's locking sequence has to be driven by a real ingest."""
    _sign_in(page, server)
    page.click("[data-goto='forge']")
    page.wait_for_function("() => keeneye.world.current === 'forge' && !keeneye.world.flight",
                           timeout=30_000)

    upload = tmp_path / "usb-key.img"
    upload.write_bytes(b"\xaaUSB\x55" * 4000)

    occupied_before = page.evaluate("keeneye.scenes.forge.cells.filter(c => c.occupied).length")

    page.set_input_files("#intake input[type=file]", str(upload))
    page.click("#intake button[type=submit]")

    # The slab is only considered sealed once it has flown into a cell.
    page.wait_for_function(
        f"() => keeneye.scenes.forge.cells.filter(c => c.occupied).length > {occupied_before}",
        timeout=60_000,
    )
    page.wait_for_function("() => keeneye.scenes.forge.slabs.length > 0", timeout=60_000)

    sealed = page.evaluate("keeneye.scenes.forge.slabs.at(-1).digest")
    assert len(sealed) == 64

    import hashlib

    assert sealed == hashlib.sha256(upload.read_bytes()).hexdigest()
    assert page.errors == []


def test_chain_verification_runs_a_pulse_down_the_ledger(page, server):
    _sign_in(page, server)
    page.click("[data-goto='ledger']")
    page.wait_for_function("() => keeneye.world.current === 'ledger' && !keeneye.world.flight",
                           timeout=30_000)

    assert page.evaluate("keeneye.scenes.ledger.blocks.length") > 0

    page.click("#run-verify")
    page.wait_for_function("() => keeneye.scenes.ledger.pulse !== null", timeout=30_000)

    assert page.evaluate("keeneye.scenes.ledger.verification.ok") is True
    assert "intact" in page.inner_text(".toast").lower()


def test_the_case_web_links_every_node_to_the_case(page, server):
    _sign_in(page, server)
    page.click("[data-goto='web']")
    page.wait_for_function("() => keeneye.world.current === 'web' && !keeneye.world.flight",
                           timeout=30_000)

    nodes = page.evaluate("keeneye.scenes.web.nodes.length")
    links = page.evaluate("keeneye.scenes.web.links.length")
    assert nodes >= 2 and links >= 1

    groups = page.evaluate("[...new Set(keeneye.scenes.web.nodes.map(n => n.definition.group))]")
    assert "case" in groups and "evidence" in groups


def test_evidence_mode_renders_the_log_without_webgl(page, server):
    """The fallback has to work, and has to be reading the same API."""
    _sign_in(page, server)
    page.goto(f"{server}/?view=flat", wait_until="networkidle")
    page.wait_for_selector(".flatroot table", timeout=30_000)

    body = page.inner_text(".flatroot")
    assert "evidence mode" in body.lower()
    assert "INTACT" in body
    assert "custody log" in body.lower()
    assert page.query_selector("#stage") is None   # no canvas at all
