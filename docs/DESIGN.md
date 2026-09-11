# Design notes

The decisions worth arguing about, and what the tamper-evidence claim actually covers.

---

## The threat model

KEENEYE is built against an examiner or administrator who has already been trusted with the system and
later wants to change what it says — the insider, not the outsider. That is the threat a chain of
custody exists for. It shapes everything below.

**In scope:** altering a stored evidence file; editing, deleting or reordering a historical custody
entry; doing either through the application, or with direct filesystem and database access.

**Out of scope:** an attacker who controls the machine at the moment evidence is ingested (they can
simply submit different bytes and the tool will faithfully record *those*); network interception (the
tool has no network dependencies); and an attacker who has the database and can recompute the entire
chain from a chosen point — see [the truncation gap](#what-the-chain-does-not-prove).

---

## Why the log is append-only *and* hash-chained

These do different jobs and neither is sufficient alone.

**The SQLite triggers** abort `UPDATE` and `DELETE` on `custody_log`. This is a constraint on the
*application*: it means no code path — including one added carelessly next year — can rewrite history
through the ordinary connection. It is not a security boundary. Anyone with the `.sqlite` file can
`DROP TRIGGER` in one line, and `tools/tamper_demo.py` does exactly that to show it.

**The hash chain** is what survives that:

```
entry_hash = SHA256( canonical_json(entry fields) || previous entry_hash )
```

Editing entry *n* changes `entry_hash(n)`. Entry *n+1* still records the old value in its `prev_hash`,
so verification can point at *n* by number. The edit leaves no trace in the row — it leaves one in the
arithmetic.

Calling the triggers "tamper-proof storage" would be the kind of overclaim that gets a tool dismissed
in cross-examination. They are a correctness guardrail. The chain is the evidence.

### Three implementation details that are load-bearing

**The hashed field list is explicit.** `chain.CHAINED_FIELDS` is written out by hand. Hashing
`dict(row)` would be shorter and would mean that adding a column in a future migration silently
invalidates every chain written before it — a failure that would surface as "the evidence doesn't
verify" long after anyone remembered the schema change.

**Serialization is canonical.** Sorted keys, no incidental whitespace, UTF-8, explicit field order.
The same entry has to produce the same bytes on any machine, or a chain written on one and verified on
another would fail for reasons that have nothing to do with tampering.

**`seq` is assigned by the application, inside the write lock.** It is one of the hashed fields, so it
must be known before the hash can be computed — `AUTOINCREMENT` assigns too late. Reading the tail and
inserting both happen inside `BEGIN IMMEDIATE`, so two concurrent writers cannot claim the same
predecessor and fork the chain. Sequence numbers are therefore contiguous from 1, which is also what
lets verification detect a *deleted* entry rather than only an edited one.

---

## What the chain does not prove

A bare hash chain proves that **no entry was altered**. It does not prove that **no entries were
removed from the end**. Truncate the log after entry 40 and the surviving 1–40 remain internally
perfectly consistent; there is a test asserting exactly this
(`test_truncating_the_tail_is_detected_only_by_the_head_hash`).

The fix is to pin the head somewhere outside the system. The court report prints the chain head in
section 2 and records the report's own digest in its metadata. A dated report that has left the
building and says the head was `ca1348…8ce6b7` is what makes a later truncation visible: the log's head
no longer matches. That is the same role a notary or a timestamp authority plays, done with a
document instead.

The honest version of the claim, then: *nothing in this log has been edited, and nothing has been
removed from the end since the last report was issued.* The tool says this in the CLI output rather
than leaving it implied.

**Related limit:** entries are chained but not signed. Someone who can write to the database can
append a valid new entry under any username. Per-examiner signing keys are the fix and are listed with
the other gaps in [STANDARDS.md](STANDARDS.md).

---

## Serving files somebody else uploaded

This application hands back attacker-supplied bytes on the same origin as the examiner's session. An
uploaded `.svg` or `.html` rendered inline would execute in that session.

- Evidence is **always** `Content-Disposition: attachment`, always `application/octet-stream`, never
  inline — regardless of what it appears to be.
- Attachments render inline only for an allow-list of raster MIME types (`jpeg`, `png`, `gif`, `webp`).
  **SVG is deliberately absent**: it is a scriptable document format that happens to draw pictures.
  There is a test asserting an SVG is forced to download.
- Every response carrying uploaded bytes gets `X-Content-Type-Options: nosniff` and its own
  `Content-Security-Policy: default-src 'none'; sandbox`.
- The page itself runs under `script-src 'self' 'nonce-…'` with no `unsafe-inline`. The one inline
  script is the three.js import map, which carries the request nonce.

The original filename never becomes a path. Files are stored under their digest, so a name like
`../../etc/passwd` is a string in a database column and nothing more.

---

## Why one WebGL canvas instead of pages

The six views are regions at fixed positions in a single `THREE.Scene`, and navigating flies the
camera between them. The alternative — a page per view, or a scene swapped per view — would tear down
and rebuild the context on every navigation, and the whole premise is that the vault is a place rather
than a set of screens.

Three consequences worth knowing about:

**Camera flights run on wall-clock time, not accumulated frame deltas.** Per-frame `dt` is clamped to
50 ms so a stalled tab cannot teleport the simulation, but that clamp would stretch a 1.9-second
flight into ten seconds on a slow renderer — and until a flight ends, the region being left is still
drawn on top of the one being flown to. Navigation has to finish on time regardless of frame rate.

**Labels are DOM, scoped by layer.** All text is `CSS2DObject`, never geometry: hashes stay selectable,
the interface stays reachable by a screen reader, and digests stay legible at any zoom. `CSS2DRenderer`
tests an object's own `visible` flag and *ignores its ancestors'*, so a label inside a hidden group
keeps drawing over whatever is in front of the camera. Each region's labels are therefore assigned
their own three.js layer and the camera enables only the active one — which is the mechanism designed
for this, and leaves scenes free to hide their own labels without a visibility-stashing scheme
fighting them.

**The vignette's `darkness` must not exceed 1.0.** The shader mixes toward `vec3(1.0 - darkness)`, so
anything above 1 drives the corners *negative*. Harmless in a clamped 8-bit buffer — and the composer
uses a half-float target, which preserves the negative value until `OutputPass` tone-maps it into a
pale grey wash across the entire frame. Depth of vignette comes from `offset` instead. This cost an
hour and is written down so it costs nobody else one.

---

## Evidence mode is not a downgrade

The plain view runs on exactly the same JSON API as the 3D shell. It was tempting to render it
server-side from the database directly, which would have been less code — but then two views could
disagree about what the log says, and for an audit tool that is the one unacceptable outcome.

It is selected automatically when WebGL is unavailable or the OS asks for reduced motion, and is
reachable any time from the toolbar. A fully 3D interface genuinely fights what makes a forensic tool
credible — an examiner needs to read a custody log fast, and a court needs a plain record. The 3D
world is how the tool is used; the PDF and this view are how it is read.

---

## Generated assets

The original plan was to generate 3D source assets (the hooded figure, a vault door, a key) with an
image-to-3D service and vendor the resulting GLBs. That service turned out to require a paid plan, so
**no generated assets exist in this repository**.

This changed nothing visually, because the plan required every scene to have a procedural fallback and
forbade any scene from depending on a generated asset existing. Everything on screen is built in code:
the circuit-board floor and the glowing filaments are GLSL, the hooded figure is a point cloud sampled
from merged primitives, the key's teeth are extruded from a live SHA-256 of the access key, and the
sigil's rim is cut from the chain head. Nothing is downloaded, and there is not a single binary asset
in the repository outside the screenshots.

`GLTFLoader` is vendored and the hook is still there, so dropping a GLB in later is a small change.

---

## Things deliberately not done

**No ORM.** The schema is four tables and the queries are simple. An ORM would put a layer between the
code and the exact SQL that enforces the append-only constraint, which is the part most worth being
able to read.

**No bcrypt or argon2.** PBKDF2-HMAC-SHA256 at 600,000 iterations, from the standard library, so the
whole tool installs with `pip install -r requirements.txt` and no compiler. The iteration count is
stored per user and can be raised without invalidating existing credentials.

**No flask-wtf.** CSRF is about twenty lines: a `secrets.token_urlsafe` token in the session, injected
into the page, compared in `before_request` on every unsafe method. Fewer dependencies in a tool like
this is worth more than the convenience.

**No npm.** three.js is vendored by `tools/vendor_three.py`, which walks the transitive closure of
relative imports from the modules actually used — 18 files, 1.4 MB. There is no build step, no
`node_modules`, and the pinned bytes are the bytes that render.
