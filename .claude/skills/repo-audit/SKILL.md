---
name: repo-audit
description: Audit an untrusted third-party repository for malware, backdoors, and misrepresentation BEFORE installing, building, or running it. Use this whenever the user pastes clone-and-install instructions and asks whether it is safe, asks you to "check this repo," "look for anything malicious," "see if there are bugs or backdoors," vet a dependency or a fork, or review code they found on GitHub/npm/PyPI and are about to run. Trigger even when the user frames it casually ("is this legit?", "should I install this?") or asks only about bugs — the install step is the risk, and it is worth checking before they run it. Do NOT use for reviewing the user's own code or diffs; that is code-review.
---

# Auditing an untrusted repository

The user is about to run someone else's code. Your job is to tell them whether that is safe,
and to be specific enough that they can act on the answer.

## The one rule

**Never run the install.** `npm install`, `pip install -e .`, `cargo build`, `pnpm install` —
these execute arbitrary code from the package. If a repo is malicious, the install *is* the
attack. Clone read-only into a scratch directory and read.

This holds even when the user asks you to install it. Audit first, report, let them decide.
If they reaffirm after seeing your findings, that's their call.

## Why this is tractable

A real repo can be thousands of files. You cannot read them all, and pretending to is worse
than useless. Two things collapse the problem:

1. **Install-time execution is a tiny surface.** For a drive-by compromise the payload has to
   run when the user follows the instructions. That means lifecycle hooks, build scripts, and
   dependency sourcing — a handful of files you can read completely.
2. **Most suspicious repos are forks.** If you can find upstream, `diff` turns "audit 5,000
   files" into "audit the 60 that differ." This is the single highest-leverage move available.

Work outward from blast radius. Finish each layer before going deeper, and stop when you can
answer the user's actual question.

## Workflow

### 1. Clone read-only, take inventory

```bash
cd "$SCRATCHPAD" && git clone --depth 50 <url> audit
```

Get the shape before the content: file count by extension, total size, largest files,
`git log` with authors and dates, root directory listing.

Three provenance signals matter and cost nothing:

- **A squashed "Initial commit" containing a mature codebase.** A 5,000-file first commit is an
  import of someone else's work with the history deleted. Note it and find the original.
- **Author identity vs. repo owner.** Commits from names unrelated to the account raise the
  question of who actually controls this.
- **Checked-in binaries.** `.node`, `.so`, `.dll`, `.exe`, prebuilt archives. You cannot read
  these. Their presence is itself a finding — say so rather than implying you reviewed them.

### 2. Enumerate the install-time execution surface

Read every script that runs on install, completely, in full. This is the highest-value reading
you will do.

```bash
python3 scripts/install_hooks.py <repo>
```

It covers npm/pnpm/yarn lifecycle hooks, Python `setup.py`/`pyproject` build backends, Cargo
`build.rs`, gem extensions, Go generate, Makefiles and CI. See
`references/install-execution-surface.md` for what executes in each ecosystem and why.

Then check how dependencies are sourced, which is the other half of install-time trust:

- Lockfile entries pointing at git URLs, raw tarballs, or a non-default registry
- `.npmrc` / `pip.conf` / `.cargo/config` redirecting the registry
- Patch files (`patches/`, `patch-package`) — these inject code into dependencies at install
- Vendored or overridden packages shadowing real ones

A lockfile that resolves everything to the public registry is a genuinely good sign. Say so.

### 3. Sweep for the things that are never innocent

```bash
bash scripts/danger_sweep.sh <repo>
```

Credential paths (`~/.ssh`, `~/.aws`, browser cookie stores, wallets, keychains), `curl | sh`,
outbound hosts, dynamic evaluation, base64 blobs, minified lines hiding in source.

Expect false positives and run them down rather than reporting them raw. `eval` in a plugin
loader and `atob` in an attachment decoder are architecture, not malware. What distinguishes a
finding is **a credential source wired to a network sink** — read enough of the surrounding
code to establish whether that wiring exists.

### 4. Find upstream and diff — the decisive step

If step 1 suggested a fork (squashed import, rebrand, a LICENSE naming someone else), find the
original and compare. Read the LICENSE, `THIRD_PARTY_NOTICES`, package scope names, and stray
identifiers the rebrand missed — renames are rarely complete, and the leftovers name the source.

```bash
git clone --depth 1 <upstream> upstream
git -C upstream fetch --unshallow --filter=blob:none   # history without blob cost
```

**Locate the fork point by tree hash.** Git names identical trees identically, so a matching
tree hash is *proof* that an entire subtree is byte-for-byte unmodified — far stronger and
cheaper than reading files:

```bash
FT=$(git -C audit rev-parse HEAD:packages/sandbox)
for c in $(git -C upstream log --format=%H --since=2026-08-01 --until=2026-09-01); do
  [ "$(git -C upstream rev-parse "$c:packages/sandbox" 2>/dev/null)" = "$FT" ] && echo "MATCH $c"
done
```

Run this against the security-critical subtrees first — sandbox, auth, crypto, network, build.
"The sandbox is byte-identical to upstream" is a sentence worth a hundred lines of prose.

Then diff the whole tree, normalizing the rebrand first so it does not drown you in noise:

```bash
git -C audit    archive HEAD           | (mkdir -p FW && tar -x -C FW)
git -C upstream archive <base-commit>  | (mkdir -p UW && tar -x -C UW)
bash scripts/fork_diff.sh FW UW old-name new-name @old-scope @new-scope "Old Name" "New Name"
```

Every trailing argument is a rebrand token folded to a common string before comparison. Pass
the scope-prefixed forms as well as the bare ones; the script sorts longest-first so they nest
correctly. It then classifies each file NEW (fork-authored — read all of these), DIFF (changed
— read the security-relevant ones), or GONE (dropped).

Normalization is what makes this usable: on a real rebrand it cut the changed-file count from
2,315 to 117, and reported 3,248 files provably identical to upstream.

Note which direction a difference runs. A fork pinned to an old snapshot will be *missing*
upstream changes; that is staleness, not tampering, and it is a real finding in its own right
because upstream security fixes are not reaching the user.

### 5. Trace reachability before you call anything a finding

This is what separates a useful audit from a scare. When you find alarming code, establish
whether it actually executes in the path the user will run:

- Is the dangerous default overridden by a config the shipped build pins?
- Does a schema default make the fallback unreachable?
- Is the file imported by anything on the entry path?

Dead code still tells you about intent and still belongs in the report — but report it as
dormant, and explain the precise condition that would wake it. Overstating severity burns the
user's trust and buries the findings that are real. Understating it is worse. Do the work to
know which one you have.

## Reporting

Lead with the verdict, because that is the question that was asked. Then structure it so a
reader can check your work:

```
## Bottom line
<Safe to install / Safe with caveats / Do not install> — one or two sentences.

## What it actually is
Provenance: origin, fork status, upstream, staleness, whether it matches its own description.

## Install/build path
A table of mechanical checks with results. Include the passes, not only the failures —
"postinstall scripts are byte-identical to upstream" is information.

## Findings
Each with: file:line, what it does, and whether it is live or dormant with the reachability
argument.

## What I did not check
Transitive dependencies, binaries, anything you could not reach. Be explicit.

## Recommendation
Concrete next step.
```

Two habits keep the report honest:

**Report the negatives.** A list of things you checked that came back clean is what makes the
verdict credible and shows the audit had a shape.

**State the boundary.** You almost never audit transitive dependencies — that requires
installing, which is the thing you refused to do. Say that plainly. An audit that implies more
coverage than it has is the failure mode that gets someone owned.

## Beyond malware

The user asked if it is safe; answer the broader question they meant. Misrepresentation is a
real finding even with clean code: a project that strips upstream attribution, a security tool
that is a prompt swap over a general-purpose harness, a stale fork that will never get upstream
fixes, a README whose claims the code does not support. Users installing an unknown repo want to
know what they are getting, not only whether it will hurt them.
