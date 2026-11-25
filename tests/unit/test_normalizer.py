"""Unit tests for Dual-Mode ArtifactNormalizer."""

import io
import tarfile
import time
import zipfile
from pathlib import Path
from backend.app.comparison.hasher import ArtifactHasher
from backend.app.normalization.normalizer import ArtifactNormalizer


def test_tar_normalization_timestamps_and_ordering(tmp_path: Path):
    """Verify that two TAR archives with identical content but different timestamps and member order

    diverge under Strict comparison, but match under Normalized comparison.
    """
    tar1 = tmp_path / "build_a.tar"
    tar2 = tmp_path / "build_b.tar"

    data_a = b"File Alpha Content"
    data_b = b"File Beta Content"

    # Archive 1: added Alpha then Beta with timestamp T1
    with tarfile.open(tar1, "w") as tf1:
        info_a = tarfile.TarInfo(name="alpha.txt")
        info_a.size = len(data_a)
        info_a.mtime = 1500000000
        info_a.mode = 0o664
        tf1.addfile(info_a, io.BytesIO(data_a))

        info_b = tarfile.TarInfo(name="beta.txt")
        info_b.size = len(data_b)
        info_b.mtime = 1500000000
        info_b.mode = 0o664
        tf1.addfile(info_b, io.BytesIO(data_b))

    # Archive 2: added Beta then Alpha with timestamp T2
    with tarfile.open(tar2, "w") as tf2:
        info_b = tarfile.TarInfo(name="beta.txt")
        info_b.size = len(data_b)
        info_b.mtime = 1700000000  # Different timestamp
        info_b.mode = 0o644        # Different mode
        tf2.addfile(info_b, io.BytesIO(data_b))

        info_a = tarfile.TarInfo(name="alpha.txt")
        info_a.size = len(data_a)
        info_a.mtime = 1700000000  # Different timestamp
        info_a.mode = 0o644        # Different mode
        tf2.addfile(info_a, io.BytesIO(data_a))

    # 1. Strict Bit-for-Bit Hashes must diverge!
    sha1 = ArtifactHasher.compute_sha256(tar1)
    sha2 = ArtifactHasher.compute_sha256(tar2)
    assert sha1 != sha2, "Strict hashes unexpectedly matched despite timestamp and order differences"

    # 2. Normalized Hashes must be identical!
    normalizer = ArtifactNormalizer()
    norm1_path = tmp_path / "norm1.tar"
    norm2_path = tmp_path / "norm2.tar"

    res1 = normalizer.normalize_tar(tar1, norm1_path)
    res2 = normalizer.normalize_tar(tar2, norm2_path)

    assert res1.normalized_sha256 == res2.normalized_sha256
    assert "SOURCE_DATE_EPOCH_MTIME" in res1.applied_rules
    assert "LEXICOGRAPHICAL_MEMBER_ORDER" in res1.applied_rules


def test_zip_normalization_timestamps(tmp_path: Path):
    """Verify that two ZIP archives with different timestamps match after normalization."""
    zip1 = tmp_path / "app1.zip"
    zip2 = tmp_path / "app2.zip"

    # Zip 1 created with date 2020-01-01
    with zipfile.ZipFile(zip1, "w") as zf1:
        info = zipfile.ZipInfo(filename="hello.txt", date_time=(2020, 1, 1, 12, 0, 0))
        zf1.writestr(info, "reproducible text payload")

    # Zip 2 created with date 2025-05-05
    with zipfile.ZipFile(zip2, "w") as zf2:
        info = zipfile.ZipInfo(filename="hello.txt", date_time=(2025, 5, 5, 12, 0, 0))
        zf2.writestr(info, "reproducible text payload")

    # Strict hashes diverge
    assert ArtifactHasher.compute_sha256(zip1) != ArtifactHasher.compute_sha256(zip2)

    # Normalization equalizes them
    normalizer = ArtifactNormalizer()
    res1 = normalizer.normalize_zip(zip1, tmp_path / "norm_app1.zip")
    res2 = normalizer.normalize_zip(zip2, tmp_path / "norm_app2.zip")

    assert res1.normalized_sha256 == res2.normalized_sha256
    assert len(res1.rule_records) == 3
    assert all(rec.version == "1.0.0" and not rec.affects_runtime_semantics for rec in res1.rule_records)


def test_meaningful_content_diff_remains_detectable_after_normalization(tmp_path: Path):
    """Master Prompt §23.6: meaningful payload differences must never be normalized away."""
    zip1 = tmp_path / "semantic_a.zip"
    zip2 = tmp_path / "semantic_b.zip"

    with zipfile.ZipFile(zip1, "w") as zf1:
        zf1.writestr(zipfile.ZipInfo(filename="app.py", date_time=(2026, 1, 1, 0, 0, 0)), "return 42\n")
    with zipfile.ZipFile(zip2, "w") as zf2:
        zf2.writestr(zipfile.ZipInfo(filename="app.py", date_time=(2026, 1, 1, 0, 0, 0)), "return 99\n")

    normalizer = ArtifactNormalizer(epoch_timestamp=1735689600)
    res1 = normalizer.normalize_zip(zip1, tmp_path / "norm_sem1.zip")
    res2 = normalizer.normalize_zip(zip2, tmp_path / "norm_sem2.zip")

    assert res1.normalized_sha256 != res2.normalized_sha256
