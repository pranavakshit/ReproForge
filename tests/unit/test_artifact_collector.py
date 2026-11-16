"""Unit tests for artifact collector."""

import hashlib
import zipfile
from pathlib import Path
import pytest
from backend.app.artifacts.collector import ArtifactCollector, compute_sha256


def test_compute_sha256(tmp_path: Path):
    """Verify cryptographic hash computation."""
    sample = tmp_path / "sample.txt"
    sample.write_bytes(b"reproforge test content")

    sha = compute_sha256(sample)
    assert len(sha) == 64
    expected_sha = hashlib.sha256(b"reproforge test content").hexdigest()
    assert sha == expected_sha


def test_collect_artifacts_from_workspace(tmp_path: Path):
    """Verify collector discovers and indexes generated wheels and zip archives."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    dist_dir = source_dir / "dist"
    dist_dir.mkdir()

    # Create dummy wheel
    wheel_file = dist_dir / "demo_app-0.1.0-py3-none-any.whl"
    wheel_file.write_bytes(b"dummy wheel payload")

    # Create dummy zip
    zip_path = source_dir / "bundle.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("test.txt", "zip test")

    collector = ArtifactCollector(base_artifacts_dir=tmp_path / "artifacts")
    artifacts = collector.collect_from_workspace(
        build_id="build-collect-1",
        workspace_source_dir=source_dir,
    )

    assert len(artifacts) == 2
    types = [a.artifact_type for a in artifacts]
    assert "python_wheel" in types
    assert "zip_archive" in types
