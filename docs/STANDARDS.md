# Standards

What this tool does, mapped to the documents that govern digital evidence handling — and, at the end,
the requirements it does not meet.

Sources: NIST SP 800-86 *Guide to Integrating Forensic Techniques into Incident Response*;
NIST SP 800-101r1 *Guidelines on Mobile Device Forensics*; ISO/IEC 27037 *Guidelines for
identification, collection, acquisition and preservation of digital evidence*; SWGDE position
documents on hashing and on best practices for computer forensics; NIST FIPS 180-4 for SHA-256 itself.

---

## 1. Hash as early as possible, with an approved algorithm

> Evidence should be hashed at the point of acquisition so that its integrity can be demonstrated at
> any later point. NIST SP 800-86 §3.1.2; ISO/IEC 27037 §6.6.

**What KEENEYE does.** A file is hashed as it streams into the vault — the same pass that writes it —
so there is no window in which an unhashed copy exists inside the system. The digest is recorded in
the evidence row and in a custody entry stamped `evidence_ingested` with `hash_check_result = pass`.

**Algorithm choice.** SHA-256 (FIPS 180-4) is the primary and only authoritative digest. SHA-1 is
computed alongside it and stored, because EnCase and FTK case files routinely carry one and an
examiner may need to cross-reference — but it is never consulted to decide whether a file is intact.
MD5 is not computed at all. SWGDE no longer considers either MD5 or SHA-1 sufficient on its own for
evidentiary purposes, and offering them would invite exactly the misuse the guidance warns against.

`coc/hashing.py`, `coc/storage.py`.

---

## 2. Documentation sufficient to uniquely identify the evidence

> Records must identify the evidence, the examiner, the time, and the tool used.
> ISO/IEC 27037 §5.4.3, §6.8; NIST SP 800-86 §3.1.2.

**What KEENEYE records**, per evidence item: case number, item number (unique within the case),
original filename, byte size, SHA-256, SHA-1, source device, acquisition date, the examiner who
acquired it, and the UTC ingest timestamp.

**On every custody entry**, without exception: the acting examiner's id and username, a UTC ISO-8601
timestamp generated server-side, the action, structured details, the hash-check outcome, and
**`tool_name` and `tool_version`**. Tool identification is an explicit requirement and is easy to omit;
it is written into the hashed portion of every entry, so it cannot be added retroactively.

`coc/db.py` (schema), `coc/service.py` (`_log`).

---

## 3. A record of everyone who accessed the evidence and every action taken

> The chain of custody is a record of custody, not a hash. ISO/IEC 27037 §6.8; SWGDE Best Practices.

Every one of these writes an entry, and none can happen without one:

| Action | Recorded |
|---|---|
| `case_opened` | case number, title |
| `evidence_ingested` | item number, filename, size, both digests, source device |
| `attachment_uploaded` | kind, filename, MIME type, size, digest, caption |
| `evidence_viewed` / `evidence_downloaded` | which item, by whom |
| `evidence_verified` | recorded vs. observed digest, pass or fail |
| `custody_transferred` / `custody_accepted` | from, to, reason |
| `report_generated` | chain status and head hash at generation |
| `user_login`, `user_created` | identity events |

A transfer is **two** entries — the handover and the acceptance — because a transfer nobody
acknowledged is a gap in the chain, not a transfer. Only the named recipient can accept one.

Attribution is enforced structurally rather than by convention: the service layer requires an
authenticated examiner for every write, and a row and its custody entry are inserted in a single
`BEGIN IMMEDIATE` transaction, so a change cannot land without the record of who made it.

---

## 4. Integrity verification at any later point

> It must be possible to demonstrate that the evidence has not changed since acquisition.
> NIST SP 800-86 §3.1.2; ISO/IEC 27037 §6.9.

`verify_evidence` re-reads the stored bytes from disk — it does not trust the path, the database, or a
cached value — recomputes SHA-256, and compares against the digest recorded at intake. The outcome is
written to the log as its own entry, **including when it fails**. A failure is not an exception the
caller can swallow: the CLI exits with status `2`, the API returns it in the response, and the
interface reports it in red for twelve seconds rather than five.

Verification can be run per item, per case, or across the whole workspace, and is schedulable:
`python -m coc verify --as auditor --json`.

---

## 5. Tamper-evident storage of the record itself

> A log that can be edited without a trace is not a record.

Two independent mechanisms, with different jobs:

1. **Append-only enforcement.** SQLite triggers abort `UPDATE` and `DELETE` on `custody_log`. This
   constrains the *application* — including a future bug in it.
2. **Hash chaining.** `entry_hash = SHA256(canonical_json(fields) || previous entry_hash)`. This is
   what survives someone bypassing the application entirely.

The hashed field list is written out explicitly in `chain.CHAINED_FIELDS` rather than derived from
whatever columns exist, because serializing the current schema would mean that adding a column later
silently invalidates every chain written before it. Serialization is canonical — sorted keys, no
incidental whitespace, UTF-8 — so an entry produces identical bytes on any machine.

What this does *not* cover, and why the report matters, is in [DESIGN.md](DESIGN.md).

---

## 6. Evidence storage

Files are stored read-only (`0444`) under their own digest. The original filename is held in the
database as data and never used as a path component, so a hostile name cannot influence where anything
is written. Writes are atomic: content is hashed into a staging file and then `os.replace`d into its
content address, so nothing is ever visible at its address in a partial state.

`coc/storage.py`.

---

## 7. Reporting

The court PDF (`coc/report.py`) contains case identification; the **integrity verdict stated in words
at the top**, not implied by a colour; the evidence inventory with full digests; the supporting-material
inventory with digests; the complete chronological custody log with each entry's `prev_hash → entry_hash`;
and an attestation naming the tool and version that produced it. Generating one is itself logged.

The report records the chain head at the moment of generation — see [DESIGN.md](DESIGN.md) for why
that specifically is what closes the last gap.

---

## What this tool does not do

Stated plainly, because a forensic tool that oversells itself is worse than one that does less:

- **No write blocker.** It hashes what it is given. If the evidence was altered before it reached
  KEENEYE, KEENEYE records the altered version as authoritative. In real practice the hash is taken at
  the point of seizure, by hardware, before anything like this is involved.
- **Not validated.** It has not been tested against NIST CFTT reference datasets, or any reference
  dataset. "The tests pass" is not the same claim as "the tool is validated for casework."
- **No cryptographic signing.** Entries are chained but not signed. Anyone who can write to the
  database can append a *valid* new entry under any username. Per-examiner signing keys would fix
  this and are not implemented.
- **No external timestamping.** Timestamps come from the server's clock. A qualified timestamp
  authority (RFC 3161) would let you prove *when* an entry was made rather than only what it said.
- **No secure deletion, retention policy, or legal hold.**
- **Single workspace, single machine.** No replication, no multi-site custody, no offline sync.
- **Not accredited** to ISO/IEC 17025 or anything else, and not intended for real casework.

The first three are the ones that would matter most to an examiner, in that order.
