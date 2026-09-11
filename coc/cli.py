"""The command line interface.

Real forensic tools are scriptable, and the parts of this one that matter most
— hashing on intake, re-verifying, walking the chain — have no business
requiring a browser. Everything here goes through the same
:class:`coc.service.Workspace` as the web front-end, so an action taken in a
terminal appears in the 3D ledger and vice versa.
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from coc import TOOL_NAME, __version__
from coc.report import build_case_report
from coc.service import ServiceError, Workspace
from coc.storage import StorageError
from coc.web.app import DEFAULT_WORKSPACE

EXIT_OK = 0
EXIT_ERROR = 1
#: Reserved for integrity failures specifically, so a CI job or a cron entry can
#: distinguish "the tool broke" from "the evidence does not verify".
EXIT_INTEGRITY = 2


# ----------------------------------------------------------------------
# output helpers
# ----------------------------------------------------------------------

def _supports_colour(stream) -> bool:
    return hasattr(stream, "isatty") and stream.isatty()


class Printer:
    """Plain text by default; colour only when a terminal is attached."""

    def __init__(self, stream=None, json_mode: bool = False) -> None:
        # Resolved here rather than as a default argument: a default is bound
        # once at import, which would pin this to whatever sys.stdout was then
        # and ignore any later redirection.
        self.stream = stream if stream is not None else sys.stdout
        self.json_mode = json_mode
        self.colour = _supports_colour(stream) and not json_mode

    def _paint(self, text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.colour else text

    def line(self, text: str = "") -> None:
        if not self.json_mode:
            print(text, file=self.stream)

    def ok(self, text: str) -> None:
        self.line(self._paint(text, "32"))

    def warn(self, text: str) -> None:
        self.line(self._paint(text, "33"))

    def bad(self, text: str) -> None:
        self.line(self._paint(text, "31;1"))

    def dim(self, text: str) -> None:
        self.line(self._paint(text, "2"))

    def data(self, payload: Any) -> None:
        if self.json_mode:
            print(json.dumps(payload, indent=2, default=str), file=self.stream)

    def table(self, headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> None:
        if self.json_mode or not rows:
            if not rows and not self.json_mode:
                self.dim("  (none)")
            return
        widths = [len(h) for h in headers]
        cells = [[str(value) for value in row] for row in rows]
        for row in cells:
            for index, value in enumerate(row):
                widths[index] = max(widths[index], len(value))
        self.line("  " + "  ".join(h.upper().ljust(widths[i]) for i, h in enumerate(headers)))
        self.line("  " + "  ".join("─" * width for width in widths))
        for row in cells:
            self.line("  " + "  ".join(value.ljust(widths[i]) for i, value in enumerate(row)))


def _progress(printer: Printer, label: str):
    """A single-line progress meter that stays quiet when piped."""
    if printer.json_mode or not printer.colour:
        return None
    state = {"last": -1}

    def report(read: int, total: int) -> None:
        if not total:
            return
        percent = int(read * 100 / total)
        if percent == state["last"]:
            return
        state["last"] = percent
        filled = percent * 28 // 100
        bar = "█" * filled + "░" * (28 - filled)
        print(f"\r  {label} {bar} {percent:3d}%", end="", file=printer.stream, flush=True)

    return report


def _finish_progress(printer: Printer, callback) -> None:
    if callback is not None:
        print("\r" + " " * 60 + "\r", end="", file=printer.stream, flush=True)


def _open(arguments) -> Workspace:
    workspace = Workspace(arguments.workspace)
    if not workspace.exists():
        raise ServiceError(
            f"no {TOOL_NAME} workspace at {workspace.root}\n"
            f"  create one with:  python -m coc init --workspace {arguments.workspace}"
        )
    return workspace


def _require_actor(workspace: Workspace, arguments):
    """Resolve --as into an examiner; every write has to be attributable."""
    username = getattr(arguments, "actor", None)
    if not username:
        raise ServiceError("who is performing this action? pass --as <username>")
    examiner = workspace.find_user(username)
    if examiner is None:
        raise ServiceError(f"no such examiner: {username}")
    return examiner


# ----------------------------------------------------------------------
# commands
# ----------------------------------------------------------------------

def cmd_init(arguments, printer: Printer) -> int:
    workspace = Workspace.initialize(arguments.workspace)
    printer.ok(f"{TOOL_NAME} workspace ready at {workspace.root}")
    printer.dim(f"  database  {workspace.db_path}")
    printer.dim(f"  vault     {workspace.vault.root}")
    printer.line()
    printer.line("Next: create an examiner —")
    printer.dim(f"  python -m coc user add --username you --role admin --workspace {arguments.workspace}")
    printer.data({"root": str(workspace.root), "db": str(workspace.db_path)})
    return EXIT_OK


def cmd_user_add(arguments, printer: Printer) -> int:
    workspace = _open(arguments)
    key = arguments.access_key or getpass.getpass("Access key (input hidden): ")
    actor = workspace.find_user(arguments.actor) if arguments.actor else None
    examiner = workspace.create_user(
        arguments.username, key, full_name=arguments.full_name, role=arguments.role, actor=actor
    )
    printer.ok(f"examiner {examiner.username} created ({examiner.role})")
    printer.data(examiner.as_dict())
    return EXIT_OK


def cmd_user_list(arguments, printer: Printer) -> int:
    workspace = _open(arguments)
    users = workspace.list_users()
    printer.line(f"{len(users)} examiner(s)")
    printer.table(
        ["username", "name", "role", "created", "active"],
        [[u["username"], u["full_name"] or "—", u["role"], u["created_at"], "yes" if u["active"] else "no"]
         for u in users],
    )
    printer.data([dict(u) for u in users])
    return EXIT_OK


def cmd_case_open(arguments, printer: Printer) -> int:
    workspace = _open(arguments)
    actor = _require_actor(workspace, arguments)
    record = workspace.open_case(arguments.number, arguments.title, actor, notes=arguments.notes)
    printer.ok(f"case {record['case_number']} opened (id {record['id']})")
    printer.data(dict(record))
    return EXIT_OK


def cmd_case_list(arguments, printer: Printer) -> int:
    workspace = _open(arguments)
    cases = workspace.list_cases()
    printer.line(f"{len(cases)} case(s)")
    printer.table(
        ["id", "number", "title", "status", "items", "files", "opened"],
        [[c["id"], c["case_number"], c["title"], c["status"],
          c["evidence_count"], c["attachment_count"], c["opened_at"]] for c in cases],
    )
    printer.data([dict(c) for c in cases])
    return EXIT_OK


def cmd_evidence_add(arguments, printer: Printer) -> int:
    workspace = _open(arguments)
    actor = _require_actor(workspace, arguments)
    case = workspace.get_case(arguments.case)

    callback = _progress(printer, "hashing")
    record = workspace.ingest_evidence(
        int(case["id"]),
        arguments.path,
        actor,
        description=arguments.description,
        item_number=arguments.item,
        source_device=arguments.source_device,
        acquisition_date=arguments.acquired,
        progress=callback,
    )
    _finish_progress(printer, callback)

    printer.ok(f"{record['item_number']} admitted to case {case['case_number']}")
    printer.line(f"  file      {record['original_filename']}  ({int(record['byte_size']):,} bytes)")
    printer.line(f"  sha256    {record['sha256']}")
    printer.dim(f"  sha1      {record['sha1']}  (legacy interoperability only)")
    printer.dim(f"  vault     {record['vault_path']}")
    printer.data(dict(record))
    return EXIT_OK


def cmd_evidence_list(arguments, printer: Printer) -> int:
    workspace = _open(arguments)
    case_id = int(workspace.get_case(arguments.case)["id"]) if arguments.case else None
    items = workspace.list_evidence(case_id)
    printer.line(f"{len(items)} evidence item(s)")
    printer.table(
        ["id", "item", "filename", "bytes", "sha256", "holder"],
        [[i["id"], i["item_number"], i["original_filename"], f"{int(i['byte_size']):,}",
          f"{str(i['sha256'])[:16]}…", workspace.current_holder(int(i["id"]))] for i in items],
    )
    printer.data([dict(i) for i in items])
    return EXIT_OK


def cmd_attach(arguments, printer: Printer) -> int:
    workspace = _open(arguments)
    actor = _require_actor(workspace, arguments)
    case = workspace.get_case(arguments.case)

    callback = _progress(printer, "hashing")
    record = workspace.attach(
        int(case["id"]),
        arguments.path,
        actor,
        evidence_id=arguments.evidence,
        kind=arguments.kind,
        caption=arguments.caption,
        mime_type=arguments.mime,
        progress=callback,
    )
    _finish_progress(printer, callback)

    printer.ok(f"{record['original_filename']} attached as {record['kind']}")
    printer.line(f"  sha256    {record['sha256']}")
    if record["evidence_id"]:
        parent = workspace.get_evidence(int(record["evidence_id"]))
        printer.dim(f"  relates to {parent['item_number']}")
    printer.data(dict(record))
    return EXIT_OK


def cmd_verify(arguments, printer: Printer) -> int:
    workspace = _open(arguments)
    actor = _require_actor(workspace, arguments)

    if arguments.case:
        results = workspace.verify_case(int(workspace.get_case(arguments.case)["id"]), actor)
    elif arguments.evidence:
        results = [workspace.verify_evidence(arguments.evidence, actor)]
    else:
        results = [
            workspace.verify_evidence(int(item["id"]), actor)
            for item in workspace.list_evidence()
        ]

    failures = [result for result in results if not result.passed]
    for result in results:
        if result.passed:
            printer.ok(f"  PASS  {result.item_number}  {result.observed_sha256}")
        else:
            printer.bad(f"  FAIL  {result.item_number}  {result.reason}")
            printer.bad(f"        recorded {result.original_sha256}")
            printer.bad(f"        observed {result.observed_sha256 or '(unreadable)'}")

    printer.line()
    if failures:
        printer.bad(f"{len(failures)} of {len(results)} item(s) FAILED verification")
    else:
        printer.ok(f"all {len(results)} item(s) verified — hashes match the values recorded at intake")

    printer.data([result.as_dict() for result in results])
    return EXIT_INTEGRITY if failures else EXIT_OK


def cmd_transfer(arguments, printer: Printer) -> int:
    workspace = _open(arguments)
    actor = _require_actor(workspace, arguments)
    recipient = workspace.find_user(arguments.to)
    if recipient is None:
        raise ServiceError(f"no such examiner: {arguments.to}")
    entry = workspace.transfer_custody(arguments.evidence, actor, recipient, reason=arguments.reason)
    printer.ok(f"custody transfer recorded: {actor.username} → {recipient.username}")
    printer.dim(f"  awaiting acceptance by {recipient.username}")
    printer.data(entry)
    return EXIT_OK


def cmd_accept(arguments, printer: Printer) -> int:
    workspace = _open(arguments)
    actor = _require_actor(workspace, arguments)
    entry = workspace.accept_custody(arguments.evidence, actor)
    printer.ok(f"custody accepted by {actor.username}")
    printer.data(entry)
    return EXIT_OK


def cmd_chain_verify(arguments, printer: Printer) -> int:
    workspace = _open(arguments)
    result = workspace.verify_chain()

    if result.ok:
        printer.ok(f"custody chain INTACT — {result.entries_checked:,} entries verified")
        printer.dim(f"  head {result.head_hash}")
        printer.dim("  Note: this proves no entry was altered. It cannot prove that entries were")
        printer.dim("  not removed from the end — compare the head above against the one printed")
        printer.dim("  in your last court report for that.")
    else:
        printer.bad(f"custody chain BROKEN at entry {result.first_broken_seq}")
        printer.bad(f"  {result.reason}")
        printer.line(f"  {result.entries_checked:,} entries examined; "
                     f"{len(result.broken_entries)} failed")

    printer.data(result.as_dict())
    return EXIT_OK if result.ok else EXIT_INTEGRITY


def cmd_log(arguments, printer: Printer) -> int:
    workspace = _open(arguments)
    case_id = int(workspace.get_case(arguments.case)["id"]) if arguments.case else None
    entries = workspace.custody_entries(case_id=case_id, evidence_id=arguments.evidence)

    printer.table(
        ["#", "timestamp (utc)", "action", "examiner", "check", "entry hash"],
        [[e["seq"], e["timestamp_utc"], str(e["action"]).replace("_", " "),
          e["actor_username"], str(e["hash_check_result"]).upper(),
          f"{str(e['entry_hash'])[:16]}…"] for e in entries],
    )
    printer.data([dict(e) for e in entries])
    return EXIT_OK


def cmd_report(arguments, printer: Printer) -> int:
    workspace = _open(arguments)
    actor = _require_actor(workspace, arguments)
    case = workspace.get_case(arguments.case)

    destination = Path(
        arguments.out or f"keeneye-{str(case['case_number']).replace('/', '-')}-report.pdf"
    )
    digest = build_case_report(workspace, int(case["id"]), destination, actor=actor)

    printer.ok(f"report written to {destination}")
    printer.line(f"  body sha256  {digest}")
    printer.dim("  The chain head is printed in section 2. Keep this document: it is what lets")
    printer.dim("  you show later that nothing was removed from the end of the log.")
    printer.data({"path": str(destination), "sha256": digest})
    return EXIT_OK


def cmd_status(arguments, printer: Printer) -> int:
    workspace = _open(arguments)
    stats = workspace.statistics()
    chain = stats["chain"]

    printer.line(f"{TOOL_NAME} {__version__} — {workspace.root}")
    printer.line()
    printer.line(f"  cases            {stats['cases']} ({stats['open_cases']} open)")
    printer.line(f"  evidence items   {stats['evidence']}")
    printer.line(f"  attachments      {stats['attachments']}")
    printer.line(f"  custody entries  {stats['custody_entries']:,}")
    printer.line(f"  vault            {stats['vault_bytes']:,} bytes")
    printer.line()
    if chain["ok"]:
        printer.ok(f"  chain INTACT     head {chain['head_hash']}")
    else:
        printer.bad(f"  chain BROKEN     first break at entry {chain['first_broken_seq']}")

    printer.data(stats)
    return EXIT_OK if chain["ok"] else EXIT_INTEGRITY


def cmd_serve(arguments, printer: Printer) -> int:  # pragma: no cover - runs a server
    from coc.web.app import create_app

    app = create_app(arguments.workspace)
    printer.ok(f"{TOOL_NAME} listening on http://{arguments.host}:{arguments.port}")
    printer.dim(f"  workspace {Path(arguments.workspace).resolve()}")
    app.run(host=arguments.host, port=arguments.port, debug=arguments.debug)
    return EXIT_OK


# ----------------------------------------------------------------------
# argument parsing
# ----------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m coc",
        description=f"{TOOL_NAME} — chain of custody for digital evidence.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exit codes:\n"
            "  0  success\n"
            "  1  the command failed\n"
            "  2  an integrity check failed — evidence or the chain did not verify\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"{TOOL_NAME} {__version__}")
    parser.add_argument("--workspace", default=DEFAULT_WORKSPACE, help="workspace directory")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--as", dest="actor", metavar="USERNAME",
                        help="the examiner performing this action")

    # The same flags again, accepted after the subcommand as well — writing
    # "evidence add file --as kim" is the natural order and should not be an
    # error. SUPPRESS is what keeps an omitted flag here from clobbering a value
    # that was given before the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workspace", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    common.add_argument("--as", dest="actor", metavar="USERNAME", default=argparse.SUPPRESS,
                        help="the examiner performing this action")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", parents=[common], help="create a workspace").set_defaults(func=cmd_init)
    sub.add_parser("status", parents=[common],
                   help="summarise the workspace and verify the chain").set_defaults(func=cmd_status)

    user = sub.add_parser("user", parents=[common], help="manage examiners").add_subparsers(
        dest="user_command", required=True)
    add_user = user.add_parser("add", parents=[common], help="create an examiner")
    add_user.add_argument("--username", required=True)
    add_user.add_argument("--full-name", default="")
    add_user.add_argument("--role", default="examiner", choices=["examiner", "admin"])
    add_user.add_argument("--access-key", help="prompted for if omitted")
    add_user.set_defaults(func=cmd_user_add)
    user.add_parser("list", parents=[common], help="list examiners").set_defaults(func=cmd_user_list)

    case = sub.add_parser("case", parents=[common], help="manage cases").add_subparsers(
        dest="case_command", required=True)
    open_case = case.add_parser("open", parents=[common], help="open a case")
    open_case.add_argument("number")
    open_case.add_argument("title")
    open_case.add_argument("--notes", default="")
    open_case.set_defaults(func=cmd_case_open)
    case.add_parser("list", parents=[common], help="list cases").set_defaults(func=cmd_case_list)

    evidence = sub.add_parser("evidence", parents=[common], help="manage evidence").add_subparsers(
        dest="evidence_command", required=True)
    add_ev = evidence.add_parser("add", parents=[common], help="hash a file and admit it as evidence")
    add_ev.add_argument("path")
    add_ev.add_argument("--case", required=True, help="case number or id")
    add_ev.add_argument("--description", default="")
    add_ev.add_argument("--item", help="item number (assigned automatically if omitted)")
    add_ev.add_argument("--source-device", default="")
    add_ev.add_argument("--acquired", help="acquisition date (YYYY-MM-DD)")
    add_ev.set_defaults(func=cmd_evidence_add)
    list_ev = evidence.add_parser("list", parents=[common], help="list evidence")
    list_ev.add_argument("--case")
    list_ev.set_defaults(func=cmd_evidence_list)

    attach = sub.add_parser("attach", parents=[common], help="attach a photograph, document or raw file")
    attach.add_argument("path")
    attach.add_argument("--case", required=True)
    attach.add_argument("--evidence", type=int, help="evidence id this supports")
    attach.add_argument("--kind", default="other", choices=["photo", "document", "raw", "other"])
    attach.add_argument("--caption", default="")
    attach.add_argument("--mime", default="application/octet-stream")
    attach.set_defaults(func=cmd_attach)

    verify = sub.add_parser("verify", parents=[common], help="re-hash evidence and compare against intake")
    verify.add_argument("--case", help="verify every item in a case")
    verify.add_argument("--evidence", type=int, help="verify one item by id")
    verify.set_defaults(func=cmd_verify)

    transfer = sub.add_parser("transfer", parents=[common], help="hand custody to another examiner")
    transfer.add_argument("evidence", type=int)
    transfer.add_argument("--to", required=True)
    transfer.add_argument("--reason", default="")
    transfer.set_defaults(func=cmd_transfer)

    accept = sub.add_parser("accept", parents=[common], help="acknowledge receipt of a transfer")
    accept.add_argument("evidence", type=int)
    accept.set_defaults(func=cmd_accept)

    chain = sub.add_parser("chain", parents=[common], help="the custody chain").add_subparsers(
        dest="chain_command", required=True)
    chain.add_parser("verify", parents=[common], help="walk the chain and confirm no link is broken").set_defaults(
        func=cmd_chain_verify)

    log = sub.add_parser("log", parents=[common], help="print the custody log")
    log.add_argument("--case")
    log.add_argument("--evidence", type=int)
    log.set_defaults(func=cmd_log)

    report = sub.add_parser("report", parents=[common], help="generate the court PDF for a case")
    report.add_argument("case")
    report.add_argument("--out", help="output path")
    report.set_defaults(func=cmd_report)

    serve = sub.add_parser("serve", parents=[common], help="run the 3D web interface")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=5000)
    serve.add_argument("--debug", action="store_true")
    serve.set_defaults(func=cmd_serve)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    printer = Printer(json_mode=arguments.json)

    try:
        return arguments.func(arguments, printer)
    except (ServiceError, StorageError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        print("\ninterrupted", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
