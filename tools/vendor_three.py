#!/usr/bin/env python3
"""Vendor the slice of three.js that KEENEYE actually uses.

three.js is committed to the repository rather than fetched at runtime for two
reasons: the app is meant to run offline on an examiner's workstation, and
pinning the exact bytes we render with matters more here than saving a megabyte.

The script walks the transitive closure of relative imports from a handful of
entry points, so the vendored tree contains everything needed to run and
nothing else.  Re-run it to change version::

    python tools/vendor_three.py --version 0.160.1
"""

from __future__ import annotations

import argparse
import io
import re
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path

REGISTRY = "https://registry.npmjs.org/three/-/three-{version}.tgz"

# What the application imports directly.  Everything else is pulled in by
# following relative imports from these.
ENTRY_POINTS = (
    "build/three.module.js",
    "examples/jsm/postprocessing/EffectComposer.js",
    "examples/jsm/postprocessing/RenderPass.js",
    "examples/jsm/postprocessing/ShaderPass.js",
    "examples/jsm/postprocessing/UnrealBloomPass.js",
    "examples/jsm/postprocessing/OutputPass.js",
    "examples/jsm/postprocessing/FilmPass.js",
    "examples/jsm/shaders/VignetteShader.js",
    "examples/jsm/renderers/CSS2DRenderer.js",
    "examples/jsm/loaders/GLTFLoader.js",
    "examples/jsm/controls/OrbitControls.js",
    "examples/jsm/utils/BufferGeometryUtils.js",
)

IMPORT_PATTERN = re.compile(
    r"""(?:^|\n)\s*(?:import|export)\b[^;'"]*?from\s*['"](\.[^'"]+)['"]""",
    re.MULTILINE,
)


def fetch_package(version: str) -> dict[str, bytes]:
    url = REGISTRY.format(version=version)
    print(f"fetching {url}")
    with urllib.request.urlopen(url, timeout=180) as response:
        payload = response.read()

    members: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            name = member.name.removeprefix("package/")
            handle = archive.extractfile(member)
            if handle is not None:
                members[name] = handle.read()
    return members


def resolve(base: str, relative: str) -> str:
    """Resolve a relative import against the importing module's path."""
    resolved = (Path(base).parent / relative).resolve()
    return str(resolved.relative_to(Path("/").resolve() / resolved.parts[1])) if False else str(
        Path(*(Path(base).parent / relative).parts)
    ).replace("\\", "/")


def normalize(path: str) -> str:
    parts: list[str] = []
    for part in path.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def closure(members: dict[str, bytes], entry_points: tuple[str, ...]) -> set[str]:
    """Every module reachable from the entry points by relative import."""
    wanted: set[str] = set()
    queue = list(entry_points)

    while queue:
        name = queue.pop()
        if name in wanted:
            continue
        if name not in members:
            raise SystemExit(f"three.js package has no {name!r} — wrong version?")
        wanted.add(name)

        source = members[name].decode("utf-8", errors="replace")
        for relative in IMPORT_PATTERN.findall(source):
            target = normalize(str(Path(name).parent / relative))
            if not target.endswith(".js"):
                target += ".js"
            queue.append(target)

    return wanted


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="0.160.1")
    parser.add_argument(
        "--dest", default="coc/web/static/vendor/three", help="where to write the tree"
    )
    arguments = parser.parse_args()

    members = fetch_package(arguments.version)
    wanted = closure(members, ENTRY_POINTS)

    destination = Path(arguments.dest)
    if destination.exists():
        shutil.rmtree(destination)

    total = 0
    for name in sorted(wanted):
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(members[name])
        total += len(members[name])

    licence = members.get("LICENSE")
    if licence:
        (destination / "LICENSE").write_bytes(licence)

    (destination / "VERSION").write_text(f"{arguments.version}\n", encoding="utf-8")
    print(f"vendored {len(wanted)} modules, {total / 1024:.0f} KiB → {destination}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
