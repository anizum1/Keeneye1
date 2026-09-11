"""The hashing engine is the foundation, so it is tested hardest."""

from __future__ import annotations

import hashlib
import io

import pytest

from coc.hashing import (
    ZERO_DIGEST,
    FileDigest,
    hash_bytes,
    hash_file,
    hash_stream,
    is_valid_sha256,
)


def test_sha256_matches_hashlib(tmp_path):
    payload = b"chain of custody" * 1000
    path = tmp_path / "evidence.bin"
    path.write_bytes(payload)

    assert hash_file(path).sha256 == hashlib.sha256(payload).hexdigest()
    assert hash_file(path).sha1 == hashlib.sha1(payload).hexdigest()
    assert hash_file(path).byte_size == len(payload)


def test_one_flipped_byte_changes_the_digest(tmp_path):
    """The property the entire tool rests on."""
    original = bytearray(b"A" * 10_000)
    path = tmp_path / "before.bin"
    path.write_bytes(bytes(original))
    before = hash_file(path).sha256

    original[5000] ^= 0x01  # flip a single bit in a single byte
    path.write_bytes(bytes(original))
    after = hash_file(path).sha256

    assert before != after


def test_chunk_size_does_not_affect_the_digest():
    """Streaming must produce the same digest regardless of read size."""
    payload = b"x" * 100_000
    digests = {
        hash_stream(io.BytesIO(payload), chunk_size=size).sha256
        for size in (1, 7, 4096, 65536, 1_000_000)
    }
    assert len(digests) == 1


def test_progress_callback_reports_monotonic_totals(tmp_path):
    path = tmp_path / "big.bin"
    path.write_bytes(b"z" * (3 * 1024 * 1024 + 17))

    seen: list[int] = []
    digest = hash_file(path, progress=lambda read, total: seen.append(read))

    assert seen == sorted(seen)
    assert seen[-1] == digest.byte_size


def test_empty_file_still_hashes(tmp_path):
    path = tmp_path / "empty.bin"
    path.write_bytes(b"")
    digest = hash_file(path)

    assert digest.byte_size == 0
    assert digest.sha256 == hashlib.sha256(b"").hexdigest()


def test_sha1_can_be_skipped():
    assert hash_bytes(b"data", with_sha1=False).sha1 is None


def test_digest_comparison_accepts_both_forms():
    digest = hash_bytes(b"data")
    assert digest.matches(digest.sha256)
    assert digest.matches(FileDigest(digest.sha256, None, 4))
    assert not digest.matches("0" * 64)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("a" * 64, True),
        (ZERO_DIGEST, True),
        ("A" * 64, False),  # uppercase is not our canonical form
        ("a" * 63, False),
        ("g" * 64, False),
        (None, False),
        (12345, False),
    ],
)
def test_is_valid_sha256(value, expected):
    assert is_valid_sha256(value) is expected
