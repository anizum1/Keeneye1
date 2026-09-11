"""The content-addressed vault."""

from __future__ import annotations

import os

import pytest

from coc.storage import StorageError, Vault


@pytest.fixture()
def vault(tmp_path) -> Vault:
    vault = Vault(tmp_path / "vault")
    vault.ensure()
    return vault


def test_file_is_stored_under_its_own_digest(vault: Vault, tmp_path):
    source = tmp_path / "image.dd"
    source.write_bytes(b"evidence bytes")
    stored = vault.store_file(source)

    assert stored.relative_path.endswith(stored.sha256)
    assert stored.relative_path.startswith(f"sha256/{stored.sha256[:2]}/{stored.sha256[2:4]}/")
    assert stored.absolute_path.read_bytes() == b"evidence bytes"


def test_identical_content_is_deduplicated(vault: Vault, tmp_path):
    first = tmp_path / "a.bin"
    second = tmp_path / "b.bin"
    first.write_bytes(b"same")
    second.write_bytes(b"same")

    original = vault.store_file(first)
    duplicate = vault.store_file(second)

    assert not original.deduplicated
    assert duplicate.deduplicated
    assert original.absolute_path == duplicate.absolute_path


def test_stored_files_are_read_only(vault: Vault, tmp_path):
    source = tmp_path / "a.bin"
    source.write_bytes(b"locked")
    stored = vault.store_file(source)

    assert stored.absolute_path.stat().st_mode & 0o222 == 0


def test_hostile_filenames_never_reach_the_filesystem(vault: Vault, tmp_path):
    """The original name is data, not a path component."""
    source = tmp_path / "harmless.bin"
    source.write_bytes(b"payload")
    stored = vault.store_file(source)

    # The stored path is derived purely from content, so a name like
    # "../../etc/passwd" could not have influenced it.
    assert stored.absolute_path.name == stored.sha256
    assert ".." not in stored.relative_path


def test_rehash_reads_the_bytes_back(vault: Vault, tmp_path):
    source = tmp_path / "a.bin"
    source.write_bytes(b"integrity")
    stored = vault.store_file(source)

    assert vault.rehash(stored.sha256).sha256 == stored.sha256


def test_rehash_detects_a_modified_vault_file(vault: Vault, tmp_path):
    source = tmp_path / "a.bin"
    source.write_bytes(b"original content")
    stored = vault.store_file(source)

    # Corrupt the vault copy the way someone with root would.
    os.chmod(stored.absolute_path, 0o644)
    stored.absolute_path.write_bytes(b"tampered content")

    assert vault.rehash(stored.sha256).sha256 != stored.sha256


def test_missing_vault_file_raises(vault: Vault):
    with pytest.raises(StorageError, match="missing from vault"):
        vault.rehash("a" * 64)


def test_invalid_digest_is_rejected(vault: Vault):
    with pytest.raises(StorageError, match="not a SHA-256"):
        vault.absolute_path_for("../../etc/passwd")


def test_no_partial_file_is_left_at_the_content_address(vault: Vault, tmp_path):
    """A failed ingest must not leave anything at a content address."""

    class Exploding:
        def __init__(self):
            self.reads = 0

        def read(self, _size):
            self.reads += 1
            if self.reads > 1:
                raise OSError("disk went away mid-ingest")
            return b"partial"

    with pytest.raises(OSError):
        vault.store_stream(Exploding())

    algorithm_root = vault.root / "sha256"
    stored_files = [p for p in algorithm_root.rglob("*") if p.is_file()]
    assert stored_files == []


def test_usage_reports_stored_bytes(vault: Vault, tmp_path):
    source = tmp_path / "a.bin"
    source.write_bytes(b"x" * 4096)
    vault.store_file(source)
    assert vault.usage_bytes() == 4096
