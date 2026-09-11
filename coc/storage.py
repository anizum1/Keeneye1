"""The evidence vault: content-addressed, write-once file storage.

Files are stored under their own SHA-256 rather than their original name::

    vault/sha256/ab/cd/abcd...ef

Three properties fall out of that, all of which matter for evidence handling:

* The path *is* the integrity claim.  A file cannot sit at the wrong address.
* Identical content is stored once, no matter how many cases reference it.
* The original filename never touches the filesystem, so a hostile name
  (``../../etc/passwd``, a NUL byte, a 4 KB unicode string) cannot influence
  where anything is written.  Names live in the database as data.

Stored files are chmod'ed read-only.  As with the append-only triggers, that
stops this application from modifying evidence; it does not stop root.
Detecting that is what :func:`coc.service.verify_evidence` is for.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from coc.hashing import CHUNK_SIZE, FileDigest, ProgressCallback, hash_stream, is_valid_sha256

VAULT_DIRNAME = "vault"
_ALGORITHM_DIR = "sha256"

#: Read-only for everyone.  Stored evidence is never rewritten in place.
_STORED_FILE_MODE = 0o444


class StorageError(RuntimeError):
    """Raised when a file cannot be admitted to or read from the vault."""


@dataclass(frozen=True)
class StoredFile:
    """The result of admitting one file to the vault."""

    digest: FileDigest
    relative_path: str
    absolute_path: Path
    deduplicated: bool

    @property
    def sha256(self) -> str:
        return self.digest.sha256

    @property
    def byte_size(self) -> int:
        return self.digest.byte_size


class Vault:
    """A directory of content-addressed files."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def ensure(self) -> None:
        (self.root / _ALGORITHM_DIR).mkdir(parents=True, exist_ok=True)

    def relative_path_for(self, sha256: str) -> str:
        if not is_valid_sha256(sha256):
            raise StorageError(f"not a SHA-256 digest: {sha256!r}")
        return f"{_ALGORITHM_DIR}/{sha256[:2]}/{sha256[2:4]}/{sha256}"

    def absolute_path_for(self, sha256: str) -> Path:
        return self.root / self.relative_path_for(sha256)

    def contains(self, sha256: str) -> bool:
        return self.absolute_path_for(sha256).is_file()

    def store_stream(
        self,
        stream: BinaryIO,
        *,
        total_size: int | None = None,
        progress: ProgressCallback | None = None,
    ) -> StoredFile:
        """Hash and store a stream in one pass.

        The content is written to a temporary file in the vault and hashed as
        it goes, then moved into its final address with :func:`os.replace`.
        Nothing is ever visible at its content address in a partial state.
        """
        self.ensure()
        staging = self.root / ".staging"
        staging.mkdir(parents=True, exist_ok=True)

        handle, temp_name = tempfile.mkstemp(dir=staging, prefix="ingest-")
        temp_path = Path(temp_name)
        try:
            with os.fdopen(handle, "wb") as sink:
                digest = _hash_while_copying(
                    stream, sink, total_size=total_size, progress=progress
                )

            destination = self.absolute_path_for(digest.sha256)
            if destination.is_file():
                temp_path.unlink(missing_ok=True)
                return StoredFile(
                    digest=digest,
                    relative_path=self.relative_path_for(digest.sha256),
                    absolute_path=destination,
                    deduplicated=True,
                )

            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(temp_path, destination)
            os.chmod(destination, _STORED_FILE_MODE)
            return StoredFile(
                digest=digest,
                relative_path=self.relative_path_for(digest.sha256),
                absolute_path=destination,
                deduplicated=False,
            )
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise

    def store_file(
        self,
        source: str | Path,
        *,
        progress: ProgressCallback | None = None,
    ) -> StoredFile:
        """Copy a file on disk into the vault, hashing it on the way in."""
        source = Path(source)
        if not source.is_file():
            raise StorageError(f"not a readable file: {source}")
        total = source.stat().st_size
        with source.open("rb") as handle:
            return self.store_stream(handle, total_size=total, progress=progress)

    def rehash(
        self,
        sha256: str,
        *,
        progress: ProgressCallback | None = None,
    ) -> FileDigest:
        """Re-read a stored file from disk and hash it again.

        This is the operation that proves integrity over time, so it
        deliberately reads the bytes back rather than trusting the path.
        """
        path = self.absolute_path_for(sha256)
        if not path.is_file():
            raise StorageError(f"evidence missing from vault: {sha256}")
        total = path.stat().st_size
        with path.open("rb") as handle:
            return hash_stream(handle, total_size=total, progress=progress)

    def open_stored(self, sha256: str) -> BinaryIO:
        path = self.absolute_path_for(sha256)
        if not path.is_file():
            raise StorageError(f"evidence missing from vault: {sha256}")
        return path.open("rb")

    def usage_bytes(self) -> int:
        algorithm_root = self.root / _ALGORITHM_DIR
        if not algorithm_root.is_dir():
            return 0
        return sum(f.stat().st_size for f in algorithm_root.rglob("*") if f.is_file())


def _hash_while_copying(
    source: BinaryIO,
    sink: BinaryIO,
    *,
    total_size: int | None,
    progress: ProgressCallback | None,
) -> FileDigest:
    """Stream ``source`` into ``sink`` and hash it in the same pass."""
    import hashlib

    sha256 = hashlib.sha256()
    sha1 = hashlib.sha1()
    read = 0

    while True:
        chunk = source.read(CHUNK_SIZE)
        if not chunk:
            break
        sink.write(chunk)
        sha256.update(chunk)
        sha1.update(chunk)
        read += len(chunk)
        if progress is not None:
            progress(read, total_size or 0)

    sink.flush()
    os.fsync(sink.fileno())
    return FileDigest(sha256=sha256.hexdigest(), sha1=sha1.hexdigest(), byte_size=read)


def purge_staging(root: str | Path) -> None:
    """Remove any half-written ingests left behind by a crash."""
    staging = Path(root) / ".staging"
    if staging.is_dir():
        shutil.rmtree(staging, ignore_errors=True)
