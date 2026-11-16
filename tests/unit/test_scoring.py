"""Unit tests for Reproducibility Assessment and CRI Calculator."""

import pytest
from backend.app.comparison.diff import ArtifactComparisonSummary
from backend.app.diagnostics.evidence import DiagnosticContext
from backend.app.schemas.entities import (
    ConfidenceLevel,
    DiagnosticCategory,
    DiagnosticFindingItem,
    ReproducibilityVerdict,
)
from backend.app.scoring.calculator import ReproducibilityCalculator
from tests.unit.test_diagnostics import make_sample_fingerprint


def test_verdict_bit_for_bit_reproducible():
    """Verify exact match produces BIT_FOR_BIT_REPRODUCIBLE verdict and CRI=1.0."""
    summary = ArtifactComparisonSummary(
        artifact_1_path="a1.zip",
        artifact_2_path="a2.zip",
        strict_sha_1="hash1",
        strict_sha_2="hash1",
        normalized_sha_1="hash1",
        normalized_sha_2="hash1",
        exact_match=True,
        normalized_match=True,
        content_agreement_ratio=1.0,
        metadata_agreement_ratio=1.0,
        total_files_evaluated=5,
        added_files_count=0,
        removed_files_count=0,
        modified_content_count=0,
        modified_metadata_count=0,
        findings=[],
    )
    assessment = ReproducibilityCalculator.evaluate(summary, diagnostics=[])

    assert assessment.verdict == ReproducibilityVerdict.BIT_FOR_BIT_REPRODUCIBLE
    assert assessment.exact_match is True
    assert assessment.composite_reproducibility_index == 1.0


def test_verdict_normalized_reproducible():
    """Verify normalized match with timestamp delta produces NORMALIZED_REPRODUCIBLE verdict."""
    summary = ArtifactComparisonSummary(
        artifact_1_path="a1.tar",
        artifact_2_path="a2.tar",
        strict_sha_1="h1",
        strict_sha_2="h2",
        normalized_sha_1="norm_h",
        normalized_sha_2="norm_h",
        exact_match=False,
        normalized_match=True,
        content_agreement_ratio=1.0,
        metadata_agreement_ratio=0.0,
        total_files_evaluated=5,
        added_files_count=0,
        removed_files_count=0,
        modified_content_count=0,
        modified_metadata_count=5,
        findings=[],
    )
    assessment = ReproducibilityCalculator.evaluate(summary, diagnostics=[])

    assert assessment.verdict == ReproducibilityVerdict.NORMALIZED_REPRODUCIBLE
    assert assessment.exact_match is False
    assert assessment.normalized_match is True
    # 0.40(0) + 0.30(1) + 0.20(1) + 0.10(0) = 0.50
    assert assessment.composite_reproducibility_index == 0.50


def test_verdict_partially_reproducible():
    """Verify partial content agreement produces PARTIALLY_REPRODUCIBLE verdict."""
    summary = ArtifactComparisonSummary(
        artifact_1_path="a1.tar",
        artifact_2_path="a2.tar",
        strict_sha_1="h1",
        strict_sha_2="h2",
        normalized_sha_1="n1",
        normalized_sha_2="n2",
        exact_match=False,
        normalized_match=False,
        content_agreement_ratio=0.80,
        metadata_agreement_ratio=0.80,
        total_files_evaluated=10,
        added_files_count=1,
        removed_files_count=1,
        modified_content_count=0,
        modified_metadata_count=0,
        findings=[],
    )
    assessment = ReproducibilityCalculator.evaluate(summary, diagnostics=[])

    assert assessment.verdict == ReproducibilityVerdict.PARTIALLY_REPRODUCIBLE
    # 0.40(0) + 0.30(0) + 0.20(0.8) + 0.10(0.8) = 0.24
    assert assessment.composite_reproducibility_index == 0.24


def test_verdict_non_reproducible():
    """Verify zero content agreement produces NON_REPRODUCIBLE verdict."""
    summary = ArtifactComparisonSummary(
        artifact_1_path="a1.tar",
        artifact_2_path="a2.tar",
        strict_sha_1="h1",
        strict_sha_2="h2",
        normalized_sha_1="n1",
        normalized_sha_2="n2",
        exact_match=False,
        normalized_match=False,
        content_agreement_ratio=0.0,
        metadata_agreement_ratio=0.0,
        total_files_evaluated=2,
        added_files_count=1,
        removed_files_count=1,
        modified_content_count=0,
        modified_metadata_count=0,
        findings=[],
    )
    assessment = ReproducibilityCalculator.evaluate(summary, diagnostics=[])

    assert assessment.verdict == ReproducibilityVerdict.NON_REPRODUCIBLE
    assert assessment.composite_reproducibility_index == 0.0


def test_verdict_inconclusive_on_unmodeled_unknown():
    """Verify inconclusive diagnostic finding yields INCONCLUSIVE verdict."""
    summary = ArtifactComparisonSummary(
        artifact_1_path="a1.bin",
        artifact_2_path="a2.bin",
        strict_sha_1="h1",
        strict_sha_2="h2",
        normalized_sha_1="h1",
        normalized_sha_2="h2",
        exact_match=False,
        normalized_match=False,
        content_agreement_ratio=0.0,
        metadata_agreement_ratio=0.0,
        total_files_evaluated=1,
        added_files_count=0,
        removed_files_count=0,
        modified_content_count=1,
        modified_metadata_count=0,
        findings=[],
    )
    diag_unknown = DiagnosticFindingItem(
        category=DiagnosticCategory.UNKNOWN_VARIATION,
        confidence=ConfidenceLevel.INCONCLUSIVE,
        affected_paths=["a1.bin"],
        root_cause_summary="unmodeled binary delta",
        evidence_trail=[],
        suggested_remediation="triage",
    )
    assessment = ReproducibilityCalculator.evaluate(summary, diagnostics=[diag_unknown])

    assert assessment.verdict == ReproducibilityVerdict.INCONCLUSIVE
    assert assessment.unexplained_differences_count == 1


def test_environment_consistency_score():
    """Verify environment consistency calculation evaluates system, toolchains, and env vars."""
    fp1 = make_sample_fingerprint(
        os_name="Linux", arch="x86_64", python_ver="3.12.0", env_vars={"A": "1", "B": "2"}
    )
    fp2 = make_sample_fingerprint(
        os_name="Linux", arch="x86_64", python_ver="3.12.0", env_vars={"A": "1", "B": "DIFFERENT"}
    )
    summary = ArtifactComparisonSummary(
        artifact_1_path="a",
        artifact_2_path="b",
        strict_sha_1="1",
        strict_sha_2="1",
        normalized_sha_1="1",
        normalized_sha_2="1",
        exact_match=True,
        normalized_match=True,
        content_agreement_ratio=1.0,
        metadata_agreement_ratio=1.0,
        total_files_evaluated=1,
        added_files_count=0,
        removed_files_count=0,
        modified_content_count=0,
        modified_metadata_count=0,
        findings=[],
    )
    ctx = DiagnosticContext(
        comparison_summary=summary,
        env_fingerprint_1=fp1,
        env_fingerprint_2=fp2,
    )
    score = ReproducibilityCalculator.calculate_environment_consistency(ctx)

    # OS match = 1.0 (weight 0.40) -> 0.40
    # Tool match = 1.0 (weight 0.40) -> 0.40
    # Env match = 1/2 = 0.50 (weight 0.20) -> 0.10
    # Total = 0.40 + 0.40 + 0.10 = 0.90
    assert score == 0.90
