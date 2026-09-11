# KEENEYE — project memory

Chain-of-custody tracker for digital evidence: SHA-256 on intake, hash-chained append-only custody
log, court PDF, 3D examiner interface plus a CLI over one engine.

Full detail already written down — read these rather than re-deriving:
`README.md` (what/how to run) · `docs/DESIGN.md` (threat model, tamper-evidence limits, 3D notes) ·
`docs/STANDARDS.md` (NIST/ISO/SWGDE mapping, and what the tool does *not* do).

## The invariant

**`coc/service.py` is the only write path.** A row and its custody-log entry are inserted inside one
`BEGIN IMMEDIATE` transaction, so nothing can change without a record of who changed it. Never write
to the database from a route, the CLI, or anywhere else. If a new operation needs a new kind of log
entry, add an `ACTION_*` constant in `service.py` — never a bare string.

## Layout

```
coc/hashing.py   chunked SHA-256 (+SHA-1, legacy interop only, never authoritative)
coc/db.py        schema, FKs, WAL, triggers that ABORT update/delete on custody_log
coc/chain.py     CHAINED_FIELDS (explicit — never hash dict(row)), entry_hash, verify()
coc/storage.py   content-addressed write-once vault, 0444, filename is data never a path
coc/service.py   Workspace — THE ONLY WRITE PATH
coc/auth.py      PBKDF2-HMAC-SHA256, 600k iterations, stdlib only
coc/report.py    ReportLab court PDF; pins the chain head
coc/cli.py       argparse; exit 2 == integrity failure (distinct from 1 == error)
coc/web/         app.py factory · api.py JSON · views.py files · security.py CSRF+CSP
coc/web/static/js/  world.js (regions, camera) · scenes/{gate,vault,forge,web,ledger,seal}.js
tests/           hashing chain storage service web cli report browser
tools/           tamper_demo.py · seed_demo.py · vendor_three.py
```

3D regions are positions in **one** persistent `THREE.Scene`; navigating flies the camera. Never add
a second canvas or a page navigation.

## Conventions

- Runtime deps stay **Flask + ReportLab**. Dev adds pytest, ruff, playwright. Don't add more.
- three.js is **vendored** (`tools/vendor_three.py`, 18 modules). No npm, no build step, runs offline.
- Tests assert properties (one flipped bit changes the digest; tamper caught at the *exact* seq), not
  line coverage. `pytest -q` · `ruff check .`
- All 3D text is `CSS2DObject` DOM, never geometry — hashes must stay selectable.

## Hard rules

- **The repo is public.** Never commit real evidence, `.env`, or a workspace.
- Evidence downloads are always `attachment`; only jpeg/png/gif/webp may render inline. **SVG never** —
  it is a scriptable document. Tests enforce this.

## Gotchas already paid for — do not re-derive

- **Vignette `darkness` must be ≤ 1.0.** The shader mixes toward `vec3(1.0 - darkness)`; above 1 the
  corners go negative, the composer's half-float target preserves that, and `OutputPass` tone-maps it
  into a grey wash over the *whole* frame.
- **`CSS2DRenderer` ignores ancestor visibility.** Labels in a hidden group keep drawing. Region
  labels are scoped by three.js **layer**; camera enables only the active one.
- **Camera flights use wall-clock**, not accumulated frame deltas. `dt` is clamped to 50 ms, which
  would otherwise stretch a 1.9 s flight to ten seconds on a slow renderer and strand two regions
  visible at once.
- **SQLite connections are thread-local** (`Workspace._local`) — the dev server is threaded.
- **`def f(stream=sys.stdout)` binds at import.** Resolve streams at call time or output bypasses any
  redirection (this broke `--json` under pytest).
- Workspace roots are `.resolve()`d — relative paths get re-resolved against Flask's `root_path` by
  `send_file`.

## Environment (this sandbox)

- **Higgsfield and Mobbin both require paid plans** — no generated assets exist, everything on screen
  is procedural (GLSL floor, point-cloud figure, hash-extruded key). Don't plan around them.
- CDNs (cdnjs, jsdelivr) are **blocked** by egress policy; `registry.npmjs.org` and `pypi.org` work.
- Chromium for Playwright: `/opt/pw-browsers/chromium`. Never run `playwright install`.
- Headless SwiftShader selects the `low` quality tier (no bloom). Force with `?quality=high`.
- `window.keeneye = { world, scenes, state, goto, api }` is exposed for console and test use.

## State

Built, tested (108 passing), pushed to `claude/chain-of-custody-tracker-e0vnwm` @ `f2f41b1`.
**Not merged — `main` is still the empty stub. User deferred the merge; don't merge unasked.**

### Next up

1. Merge to `main` (clean fast-forward).
2. **Real-case intake** — user will load genuine documents. Local only, never committed. Likely wants
   a `coc import <folder>` bulk path.

### Known gap to close before that

`.gitignore` covers the vault, `*.sqlite*` and `.env`, but **not loose documents**. Verified today:
`evidence/warrant.pdf` and `my-cases/scene-01.jpg` would both be committed. Add quarantine patterns
(`private/`, `incoming/`, `cases/`, `evidence/`, `*-case/`) before any real material lands. Do **not**
blanket-ignore `*.pdf`/`*.jpg` — `docs/screenshots/` is tracked.

## Commands

```bash
python -m coc init && python -m coc user add --username you --role admin
python tools/seed_demo.py && python -m coc serve --workspace workspace-demo   # kim / open-sesame-2026
python tools/tamper_demo.py          # both attacks must be DETECTED; exits non-zero if not
pytest -q --ignore=tests/test_browser.py     # 99 fast; browser suite adds 9, ~100s
```

Windows: `py -m venv .venv` then call `.\.venv\Scripts\python.exe` **directly** — activating trips the
execution policy, and `python3` does not exist there.
