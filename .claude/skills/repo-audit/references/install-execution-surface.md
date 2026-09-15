# Where code executes at install time, by ecosystem

The question to answer is narrow: **if the user runs the commands in the README, what
arbitrary code executes, and from where?** Everything here is about that moment.

A repo with no install-time execution is not thereby safe — it means any payload must run
at *use* time instead, which moves your attention to the entry point rather than ending the
audit.

## Node — npm / pnpm / yarn

Lifecycle scripts in any `package.json` in the workspace, run automatically by `install`:

`preinstall` → `install` → `postinstall`, plus `prepare` (runs on local installs and on
`git` dependencies), `prepublish`, `prepublishOnly`, `prepack`, `postpack`.

Points worth knowing:

- **Workspaces multiply this.** Every member package's hooks run, not just the root's. Scan
  all of them.
- **Dependencies' hooks run too.** You cannot read them without installing, which is the
  boundary of this kind of audit — say so in the report.
- **pnpm ≥ 10 denies dependency scripts by default** (`strictDepBuilds`). The allowlist lives
  in `pnpm-workspace.yaml` under `allowBuilds` / `onlyBuiltDependencies`. A short, commented
  allowlist is a good sign; a blanket enable is worth flagging.
- **`patchedDependencies` / `patch-package`** apply diffs to dependency source at install.
  Read every patch — it is a code-injection point that reviewers routinely skip.
- **`overrides` / `resolutions`** can redirect a package name to `link:`, a git URL, or a
  different version. Check what they point at.
- **`binding.gyp`** means native compilation at install: a compiler runs on code you have not
  read.
- **`.npmrc`** can move the registry. A non-default registry means the lockfile's integrity
  hashes are checked against packages you have not seen.

## Python — pip / uv / poetry

- **`setup.py` executes on install**, including when pip builds an sdist. This is the classic
  vector; read it entirely.
- **`pyproject.toml`** with `build-backend` runs that backend. A standard backend
  (hatchling, setuptools, poetry-core, flit) is normal. **`backend-path` means a *local*
  backend** — code from the repo itself runs during the build. Always read those modules.
- `setup.cfg` `cmdclass` overrides can attach code to install commands.
- `conftest.py` executes on any `pytest` run, which matters if the README says to run tests.
- `pip.conf` / `PIP_INDEX_URL` can redirect the index.

## Rust — cargo

- **`build.rs` is compiled and executed before the crate builds**, with full host privileges.
  This is cargo's only install-time execution hook, and it is the whole surface.
- `.cargo/config.toml` can redirect the registry or replace sources.
- Proc-macro crates execute at *compile* time — a dependency, not the repo, but the same
  trust boundary.

## Ruby, Go, PHP, and others

- **Ruby**: `extconf.rb` and `ext/` build native extensions on `gem install`; `Rakefile` tasks
  and `Gemfile` itself are executable Ruby evaluated by bundler.
- **Go**: no install-time hooks — `go build` does not run package code. `//go:generate` runs
  only when explicitly invoked. This is a genuinely smaller surface; the risk moves to
  compile-time `init()` functions and to the binary's runtime behavior.
- **PHP/Composer**: `scripts` in `composer.json` (`post-install-cmd`, `post-autoload-dump`).
- **Java/Gradle**: `build.gradle` *is* a program, executed on every build.

## Cross-cutting

- **Makefiles, `justfile`, `Taskfile.yml`** — arbitrary shell whenever the README says to run
  them. Read the targets the instructions actually name.
- **Git hooks installed into the user's clone** — `lefthook.yml`, `.pre-commit-config.yaml`,
  `husky/`. These run on the user's later commits, not just at install. Usually benign
  developer tooling, but it is a persistence mechanism and belongs in the report.
- **`.vscode/tasks.json`, `.devcontainer/`** — can execute on folder open in an editor.
- **CI workflows** — not a risk to the user installing locally, but `pull_request_target`
  combined with a checkout of PR code is a repo-compromise vector worth flagging to a
  maintainer.
- **Editor/shell config the install writes** — anything appending to `.bashrc`, `.zshrc`,
  `.profile`, or the user's git config is persistence. Note it even when benign.

## Judging what you find

A hook existing is not a finding. Read it and ask:

1. Does it reach the network? Downloads at install time mean the audited bytes are not the
   executed bytes.
2. Does it read anything outside the repo — home directory, environment, credential stores?
3. Does it write outside the repo, especially to shell config or git config?
4. Is it obfuscated, or does it build a command from string fragments?

A hook that chmods a bundled binary or installs git hooks is doing its job. A hook that curls
a URL and pipes it to a shell is the attack. Most are the former; say so clearly when they are,
because a report that flags every hook teaches the user to ignore you.

**Strongest possible check:** if the repo is a fork, compare the install scripts to upstream's.
"Byte-identical to upstream" ends the question in a way that reading never quite does.
