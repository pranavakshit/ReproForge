"""Unit tests for ArtifactHasher."""

from pathlib import Path
from backend.app.comparison.hasher import ArtifactHasher


def test_hasher_digests(tmp_path: Path):
    """Verify SHA-256 and BLAKE2b computations."""
    sample = tmp_path / "sample.bin"
    sample.write_bytes(b"reproforge multi-algorithm hashing test")

    hashes = ArtifactHasher.compute_hashes(sample)
    assert "sha256" in hashes
    assert "blake2b" in hashes
    assert len(hashes["sha256"]) == 64
    assert len(hashes["blake2b"]) == 128


def test_are_files_identical(tmp_path: Path):
    """Verify streaming chunked byte comparison."""
    f1 = tmp_path / "f1.bin"
    f2 = tmp_path / "f2.bin"
    f3 = tmp_path / "f3.bin"

    f1.write_bytes(b"A" * 100000 + b"B")
    f2.write_bytes(b"A" * 100000 + b"B")
    f3.write_bytes(b"A" * 100000 + b"C")

    identical, offset = ArtifactHasher.are_files_identical(f1, f2)
    assert identical is True
    assert offset == -1

    identical, offset = ArtifactHasher.are_files_identical(f1, f3)
    assert identical is False
    assert offset == 100000
