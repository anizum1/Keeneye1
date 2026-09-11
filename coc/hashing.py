"""Cryptographic hashing of evidence files.

SHA-256 is the primary digest.  SWGDE no longer considers MD5 or SHA-1 strong
enough to stand alone for evidentiary purposes, so SHA-1 is computed only as an
optional secondary value for interoperability with older tooling (EnCase and
FTK case files routinely carry one) and is never used on its own to decide
whether a file is intact.

Everything here streams.  Evidence is routinely tens or hundreds of gigabytes
and must never be read into memory in one piece.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

# 1 MiB.  Large enough that syscall overhead disappears, small enough that
# progress callbacks stay responsive on a slow disk.
CHUNK_SIZE = 1024 * 1024

PRIMARY_ALGORITHM = "sha256"
SECONDARY_ALGORITHM = "sha1"

#: A SHA-256 digest of all zero bits.  Used as the ``prev_hash`` of the genesis
#: custody-log entry, where there is no previous entry to point at.
ZERO_DIGEST = "0" * 64

ProgressCallback = Callable[[int, int], None]


@dataclass(frozen=True)
class FileDigest:
    """The digests and size of a single file."""

    sha256: str
    sha1: str | None
    byte_size: int

    def matches(self, other: FileDigest | str) -> bool:
        """Compare against another digest, or against a bare SHA-256 string."""
        if isinstance(other, FileDigest):
            return self.sha256 == other.sha256
        return self.sha256 == other


def _iter_chunks(stream: BinaryIO, chunk_size: int = CHUNK_SIZE) -> Iterable[bytes]:
    while True:
        chunk = stream.read(chunk_size)
        if not chunk:
            return
        yield chunk


def hash_stream(
    stream: BinaryIO,
    *,
    with_sha1: bool = True,
    total_size: int | None = None,
    progress: ProgressCallback | None = None,
    chunk_size: int = CHUNK_SIZE,
) -> FileDigest:
    """Hash an already-open binary stream.

    ``progress`` is called with ``(bytes_read, total_size)`` after every chunk
    so a caller can drive a real progress indicator.  ``total_size`` is passed
    straight through and may be ``0`` when the caller does not know it.
    """
    sha256 = hashlib.sha256()
    sha1 = hashlib.sha1() if with_sha1 else None
    read = 0

    for chunk in _iter_chunks(stream, chunk_size):
        sha256.update(chunk)
        if sha1 is not None:
            sha1.update(chunk)
        read += len(chunk)
        if progress is not None:
            progress(read, total_size if total_size is not None else 0)

    return FileDigest(
        sha256=sha256.hexdigest(),
        sha1=sha1.hexdigest() if sha1 is not None else None,
        byte_size=read,
    )


def hash_file(
    path: str | Path,
    *,
    with_sha1: bool = True,
    progress: ProgressCallback | None = None,
    chunk_size: int = CHUNK_SIZE,
) -> FileDigest:
    """Hash a file on disk, streaming it a chunk at a time."""
    path = Path(path)
    total = path.stat().st_size
    with path.open("rb") as handle:
        return hash_stream(
            handle,
            with_sha1=with_sha1,
            total_size=total,
            progress=progress,
            chunk_size=chunk_size,
        )


def hash_bytes(data: bytes, *, with_sha1: bool = True) -> FileDigest:
    """Hash an in-memory buffer.  Intended for small values and for tests."""
    sha256 = hashlib.sha256(data).hexdigest()
    sha1 = hashlib.sha1(data).hexdigest() if with_sha1 else None
    return FileDigest(sha256=sha256, sha1=sha1, byte_size=len(data))


def is_valid_sha256(value: object) -> bool:
    """True when ``value`` looks like a lowercase hex SHA-256 digest."""
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(character in "0123456789abcdef" for character in value)
