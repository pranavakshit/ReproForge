"""Unit tests for ArchiveUnpacker and ArtifactTree."""

import io
import tarfile
import zipfile
from pathlib import Path
import pytest
from backend.app.comparison.unpacker import ArchiveUnpacker


def test_unpack_zip_archive(tmp_path: Path):
    """Verify ZIP extraction and entry cataloging."""
    zip_path = tmp_path / "test.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("dir/file1.txt", "content one")
        zf.writestr("dir/file2.txt", "content two")

    extract_dir = tmp_path / "extracted_zip"
    tree = ArchiveUnpacker.unpack_zip(zip_path, extract_dir)

    assert tree.total_files == 2
    assert tree.get_entry("dir/file1.txt") is not None
    assert tree.get_entry("dir/file2.txt") is not None
    assert tree.content_only_digest != ""
    assert (extract_dir / "dir" / "file1.txt").read_text() == "content one"


def test_unpack_tar_archive(tmp_path: Path):
    """Verify TAR extraction and metadata cataloging."""
    tar_path = tmp_path / "test.tar"
    with tarfile.open(tar_path, "w") as tf:
        data = b"hello from tar"
        info = tarfile.TarInfo(name="app/main.py")
        info.size = len(data)
        info.mtime = 1600000000
        tf.addfile(info, io.BytesIO(data))

    extract_dir = tmp_path / "extracted_tar"
    tree = ArchiveUnpacker.unpack_tar(tar_path, extract_dir)

    assert tree.total_files == 1
    entry = tree.get_entry("app/main.py")
    assert entry is not None
    assert entry.size_bytes == len(data)
    assert entry.mtime == 1600000000
    assert (extract_dir / "app" / "main.py").read_bytes() == data


def test_safe_rel_path_traversal_protection():
    """Verify path traversal attempts are rejected."""
    with pytest.raises(ValueError, match="Path traversal detected"):
        ArchiveUnpacker._safe_rel_path("../../etc/passwd")

    with pytest.raises(ValueError, match="Path traversal detected"):
        ArchiveUnpacker._safe_rel_path("..")
