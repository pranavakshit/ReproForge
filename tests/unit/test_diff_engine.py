"""Unit tests for StructuralDiffEngine."""

import zipfile
from pathlib import Path
from backend.app.comparison.diff import StructuralDiffEngine


def test_compare_identical_artifacts(tmp_path: Path):
    """Verify comparing identical artifacts reports exact match and 100% agreement."""
    zip1 = tmp_path / "a1.zip"
    zip2 = tmp_path / "a2.zip"

    for zpath in (zip1, zip2):
        with zipfile.ZipFile(zpath, "w") as zf:
            info = zipfile.ZipInfo(filename="main.py", date_time=(2024, 1, 1, 0, 0, 0))
            zf.writestr(info, "print('identical')")

    engine = StructuralDiffEngine()
    summary = engine.compare_artifacts(zip1, zip2, tmp_path / "work_identical")

    assert summary.exact_match is True
    assert summary.normalized_match is True
    assert summary.content_agreement_ratio == 1.0
    assert summary.metadata_agreement_ratio == 1.0
    assert len(summary.findings) == 0


def test_compare_timestamp_diverged_artifacts(tmp_path: Path):
    """Verify timestamp-only difference flags METADATA_ONLY and holds normalized match."""
    zip1 = tmp_path / "t1.zip"
    zip2 = tmp_path / "t2.zip"

    with zipfile.ZipFile(zip1, "w") as zf:
        info1 = zipfile.ZipInfo(filename="app.py", date_time=(2022, 1, 1, 0, 0, 0))
        zf.writestr(info1, "sample application logic")

    with zipfile.ZipFile(zip2, "w") as zf:
        info2 = zipfile.ZipInfo(filename="app.py", date_time=(2024, 6, 1, 0, 0, 0))
        zf.writestr(info2, "sample application logic")

    engine = StructuralDiffEngine()
    summary = engine.compare_artifacts(zip1, zip2, tmp_path / "work_timestamp")

    assert summary.exact_match is False
    assert summary.normalized_match is True
    assert summary.content_agreement_ratio == 1.0
    assert summary.metadata_agreement_ratio == 0.0
    assert summary.modified_metadata_count == 1
    assert len(summary.findings) == 1
    assert summary.findings[0].diff_type == "METADATA_ONLY"
    assert summary.findings[0].normalized_match is True


def test_compare_content_and_structural_divergence(tmp_path: Path):
    """Verify added, removed, and modified content files are accurately identified."""
    zip1 = tmp_path / "c1.zip"
    zip2 = tmp_path / "c2.zip"

    with zipfile.ZipFile(zip1, "w") as zf1:
        zf1.writestr("common.py", "version 1")
        zf1.writestr("old_feature.py", "to be removed")

    with zipfile.ZipFile(zip2, "w") as zf2:
        zf2.writestr("common.py", "version 2 (modified)")
        zf2.writestr("new_feature.py", "newly added")

    engine = StructuralDiffEngine()
    summary = engine.compare_artifacts(zip1, zip2, tmp_path / "work_diverged")

    assert summary.exact_match is False
    assert summary.normalized_match is False
    assert summary.content_agreement_ratio == 0.0
    assert summary.added_files_count == 1
    assert summary.removed_files_count == 1
    assert summary.modified_content_count == 1

    diff_types = {f.file_path: f.diff_type for f in summary.findings}
    assert diff_types["common.py"] == "MODIFIED_CONTENT"
    assert diff_types["old_feature.py"] == "REMOVED"
    assert diff_types["new_feature.py"] == "ADDED"
