#!/usr/bin/env python3
"""Enumerate everything in a repo that executes at install/build time.

This is the surface a drive-by compromise has to use: if following the project's
own install instructions runs attacker code, it runs through one of these.
Reads nothing into a package manager -- pure static inspection.

Usage: python3 install_hooks.py <repo-root>
"""
import json
import os
import sys

NPM_HOOKS = (
    "preinstall", "install", "postinstall",
    "preprepare", "prepare", "postprepare",
    "prepublish", "prepublishOnly", "prepack", "postpack",
    "predependencies", "dependencies",
)

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "vendor-cache", "__pycache__", "dist", "build"}


def walk(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            yield os.path.join(dirpath, fn)


def rel(root, path):
    return os.path.relpath(path, root)


def scan_npm(root, findings):
    for path in walk(root):
        if os.path.basename(path) != "package.json":
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        scripts = data.get("scripts")
        if not isinstance(scripts, dict):
            continue
        hits = [(k, v) for k, v in scripts.items() if k in NPM_HOOKS]
        if hits:
            findings.append(("npm lifecycle", rel(root, path), hits))


def scan_simple(root, findings):
    """Files whose mere existence means code runs at install/build time."""
    markers = {
        "setup.py": "python: executes on `pip install` (incl. sdist builds)",
        "conanfile.py": "conan: executes at dependency resolution",
        "build.rs": "cargo: compiled and run before the crate builds",
        "binding.gyp": "node-gyp: native build, runs compilers at install",
        "extconf.rb": "rubygems: native extension build",
        "Rakefile": "ruby: task runner, often invoked by gem install",
        "meson.build": "meson: build-time script evaluation",
        "wscript": "waf: build-time script evaluation",
        "lefthook.yml": "git hooks installed into the user's clone",
        ".pre-commit-config.yaml": "git hooks installed into the user's clone",
        "Makefile": "make: arbitrary shell if the user runs make",
        "justfile": "just: arbitrary shell if the user runs just",
    }
    for path in walk(root):
        base = os.path.basename(path)
        if base in markers:
            findings.append(("build/run hook", rel(root, path), [(base, markers[base])]))


def scan_python_backend(root, findings):
    for path in walk(root):
        if os.path.basename(path) != "pyproject.toml":
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError):
            continue
        notes = []
        if "build-backend" in text:
            for line in text.splitlines():
                if "build-backend" in line:
                    notes.append(("build-backend", line.strip()))
        # local backends execute code from the repo itself
        if "backend-path" in text:
            notes.append(("backend-path", "LOCAL build backend -- code from this repo runs on install"))
        if notes:
            findings.append(("python build", rel(root, path), notes))


def scan_dep_sourcing(root, findings):
    """Non-registry dependency sources and registry redirection."""
    config_names = {".npmrc", ".yarnrc", ".yarnrc.yml", "pip.conf", "config.toml", "config"}
    for path in walk(root):
        base = os.path.basename(path)
        if base in config_names and (".cargo" in path or base != "config"):
            try:
                with open(path, encoding="utf-8") as fh:
                    body = fh.read().strip()
            except (OSError, UnicodeDecodeError):
                continue
            if body:
                findings.append(("registry config", rel(root, path), [("contents", body[:400])]))
        if base in {"package-lock.json", "pnpm-lock.yaml", "yarn.lock", "Cargo.lock", "poetry.lock"}:
            try:
                with open(path, encoding="utf-8") as fh:
                    lines = fh.read().splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            odd = [
                ln.strip()[:160] for ln in lines
                if ("git+" in ln or "github.com" in ln or "http://" in ln)
                and "resolution" in ln.lower() or ln.strip().startswith(("resolved \"git", "resolved \"http://"))
            ]
            if odd:
                findings.append(("non-registry dep", rel(root, path), [("entry", o) for o in odd[:15]]))


def scan_patches(root, findings):
    for path in walk(root):
        if path.endswith(".patch") or path.endswith(".diff"):
            findings.append(("dependency patch", rel(root, path),
                             [("note", "injects code into a dependency at install time -- read it")]))


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    root = os.path.abspath(sys.argv[1])
    if not os.path.isdir(root):
        print(f"not a directory: {root}", file=sys.stderr)
        return 2

    findings = []
    scan_npm(root, findings)
    scan_simple(root, findings)
    scan_python_backend(root, findings)
    scan_dep_sourcing(root, findings)
    scan_patches(root, findings)

    if not findings:
        print("No install-time execution hooks found.")
        print("Note: absence of hooks does not make a repo safe -- it means the")
        print("payload, if any, must run at USE time rather than install time.")
        return 0

    by_kind = {}
    for kind, path, details in findings:
        by_kind.setdefault(kind, []).append((path, details))

    for kind in sorted(by_kind):
        print(f"\n{'=' * 70}\n{kind.upper()}\n{'=' * 70}")
        for path, details in sorted(by_kind[kind]):
            print(f"\n  {path}")
            for k, v in details:
                v = str(v).replace("\n", "\n      ")
                print(f"      {k}: {v}")

    print(f"\n{'=' * 70}")
    print("READ EVERY FILE LISTED ABOVE IN FULL before the user installs anything.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
