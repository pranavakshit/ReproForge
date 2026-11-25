"""Unit tests for Phase 4 Diagnostic Engine, classification rules, and remediation generator."""

import pytest
from backend.app.comparison.diff import ArtifactComparisonSummary
from backend.app.diagnostics.engine import DiagnosticEngine
from backend.app.diagnostics.evidence import (
    DiagnosticContext,
    EpistemicStatus,
    EvidenceItem,
    EvidenceType,
)
from backend.app.diagnostics.remediation import (
    RemediationGenerator,
    RemediationRiskLevel,
)
from backend.app.diagnostics.rules import (
    RuleBaseImageVariation,
    RuleDependencyVariation,
    RuleEnvironmentLeakage,
    RuleExternalInputVariation,
    RuleFilesystemOrdering,
    RuleGeneratedContent,
    RuleMetadataVariation,
    RulePlatformVariation,
    RuleTimestampNondeterminism,
    RuleToolchainVariation,
    RuleUnknownVariation,
)
from backend.app.orchestration.detector import DetectedBuildSystem
from backend.app.schemas.entities import (
    ConfidenceLevel,
    DiagnosticCategory,
    DiagnosticFindingItem,
    DiffFindingItem,
    EnvironmentFingerprintRead,
    LocaleInfo,
    SystemInfo,
    ToolchainInfo,
)


def make_sample_fingerprint(
    os_name="Linux",
    arch="x86_64",
    python_ver="3.12.0",
    env_vars=None,
) -> EnvironmentFingerprintRead:
    return EnvironmentFingerprintRead(
        id="fp-1",
        fingerprint_hash="a" * 64,
        system_info=SystemInfo(
            os_name=os_name,
            os_release="6.1.0",
            os_version="Debian",
            kernel_release="6.1.0",
            cpu_arch=arch,
            cpu_count=8,
            total_ram_bytes=16000000000,
            hostname_masked="node-1234",
        ),
        toolchain_info=ToolchainInfo(
            tools={"python": {"version": python_ver}}
        ),
        locale_info=LocaleInfo(timezone="UTC", umask="0022"),
        environment_variables=env_vars or {"PATH": "/bin"},
    )


def test_rule_timestamp_nondeterminism_high_confidence():
    """Verify pure metadata mtime differences trigger TIMESTAMP_NONDETERMINISM with HIGH confidence."""
    summary = ArtifactComparisonSummary(
        artifact_1_path="a1.tar",
        artifact_2_path="a2.tar",
        strict_sha_1="1" * 64,
        strict_sha_2="2" * 64,
        normalized_sha_1="abc",
        normalized_sha_2="abc",
        exact_match=False,
        normalized_match=True,
        content_agreement_ratio=1.0,
        metadata_agreement_ratio=0.0,
        total_files_evaluated=2,
        added_files_count=0,
        removed_files_count=0,
        modified_content_count=0,
        modified_metadata_count=2,
        findings=[
            DiffFindingItem(
                file_path="app.py",
                diff_type="METADATA_ONLY",
                strict_match=False,
                normalized_match=True,
                size_delta_bytes=0,
                details={"mtime_1": 1600000000, "mtime_2": 1700000000},
            )
        ],
    )
    ctx = DiagnosticContext(comparison_summary=summary)
    rule = RuleTimestampNondeterminism()
    result = rule.evaluate(ctx)

    assert result is not None
    assert result.category == DiagnosticCategory.TIMESTAMP_NONDETERMINISM
    assert result.confidence == ConfidenceLevel.HIGH
    assert result.epistemic_status == EpistemicStatus.INFERRED
    assert "app.py" in result.affected_paths


def test_rule_timestamp_with_confounding_toolchain_downgrade():
    """Verify toolchain change acts as a confounder, downgrading timestamp confidence."""
    summary = ArtifactComparisonSummary(
        artifact_1_path="a1.tar",
        artifact_2_path="a2.tar",
        strict_sha_1="1" * 64,
        strict_sha_2="2" * 64,
        normalized_sha_1="abc",
        normalized_sha_2="abc",
        exact_match=False,
        normalized_match=True,
        content_agreement_ratio=1.0,
        metadata_agreement_ratio=0.0,
        total_files_evaluated=1,
        added_files_count=0,
        removed_files_count=0,
        modified_content_count=0,
        modified_metadata_count=1,
        findings=[
            DiffFindingItem(
                file_path="app.py",
                diff_type="METADATA_ONLY",
                strict_match=False,
                normalized_match=True,
                size_delta_bytes=0,
                details={"mtime_1": 1600000000, "mtime_2": 1700000000},
            )
        ],
    )
    # Provide fingerprints where python toolchain diverged
    fp1 = make_sample_fingerprint(python_ver="3.11.0")
    fp2 = make_sample_fingerprint(python_ver="3.12.0")
    ctx = DiagnosticContext(
        comparison_summary=summary,
        env_fingerprint_1=fp1,
        env_fingerprint_2=fp2,
    )

    rule = RuleTimestampNondeterminism()
    result = rule.evaluate(ctx)

    assert result is not None
    assert result.confidence == ConfidenceLevel.MEDIUM
    assert result.evidence_bundle.has_confounders is True


def test_rule_environment_leakage_positive_match():
    """Verify leaked environment variable value in diff triggers ENVIRONMENT_LEAKAGE."""
    summary = ArtifactComparisonSummary(
        artifact_1_path="build1.zip",
        artifact_2_path="build2.zip",
        strict_sha_1="1" * 64,
        strict_sha_2="2" * 64,
        normalized_sha_1="a",
        normalized_sha_2="b",
        exact_match=False,
        normalized_match=False,
        content_agreement_ratio=0.5,
        metadata_agreement_ratio=1.0,
        total_files_evaluated=2,
        added_files_count=0,
        removed_files_count=0,
        modified_content_count=1,
        modified_metadata_count=0,
        findings=[
            DiffFindingItem(
                file_path="config.json",
                diff_type="MODIFIED_CONTENT",
                strict_match=False,
                normalized_match=False,
                size_delta_bytes=10,
                details={"diff_snippet": 'Host set to build-agent-charlie-99'},
            )
        ],
    )
    fp1 = make_sample_fingerprint(env_vars={"HOSTNAME": "build-agent-alpha-01"})
    fp2 = make_sample_fingerprint(env_vars={"HOSTNAME": "build-agent-charlie-99"})
    ctx = DiagnosticContext(
        comparison_summary=summary,
        env_fingerprint_1=fp1,
        env_fingerprint_2=fp2,
    )

    rule = RuleEnvironmentLeakage()
    result = rule.evaluate(ctx)

    assert result is not None
    assert result.category == DiagnosticCategory.ENVIRONMENT_LEAKAGE
    assert result.confidence == ConfidenceLevel.HIGH
    assert "config.json" in result.affected_paths


def test_rule_environment_leakage_negative_coincidental_difference():
    """Verify that a coincidental env var difference that does NOT appear in artifact diff does NOT trigger leakage."""
    summary = ArtifactComparisonSummary(
        artifact_1_path="b1.zip",
        artifact_2_path="b2.zip",
        strict_sha_1="1" * 64,
        strict_sha_2="2" * 64,
        normalized_sha_1="a",
        normalized_sha_2="b",
        exact_match=False,
        normalized_match=False,
        content_agreement_ratio=0.0,
        metadata_agreement_ratio=1.0,
        total_files_evaluated=1,
        added_files_count=0,
        removed_files_count=0,
        modified_content_count=1,
        modified_metadata_count=0,
        findings=[
            DiffFindingItem(
                file_path="app.bin",
                diff_type="MODIFIED_CONTENT",
                strict_match=False,
                normalized_match=False,
                size_delta_bytes=0,
                details={"diff_snippet": "unrelated binary opcode differences"},
            )
        ],
    )
    # Env vars differ, but neither value is in the snippet
    fp1 = make_sample_fingerprint(env_vars={"AGENT_ID": "agent-delta-777"})
    fp2 = make_sample_fingerprint(env_vars={"AGENT_ID": "agent-echo-888"})
    ctx = DiagnosticContext(
        comparison_summary=summary,
        env_fingerprint_1=fp1,
        env_fingerprint_2=fp2,
    )

    rule = RuleEnvironmentLeakage()
    result = rule.evaluate(ctx)
    assert result is None  # Must NOT falsely accuse env leakage!


def test_rule_dependency_variation_from_logs():
    """Verify dependency divergence confirmed via build logs triggers HIGH confidence diagnosis."""
    summary = ArtifactComparisonSummary(
        artifact_1_path="b1.whl",
        artifact_2_path="b2.whl",
        strict_sha_1="1" * 64,
        strict_sha_2="2" * 64,
        normalized_sha_1="a",
        normalized_sha_2="b",
        exact_match=False,
        normalized_match=False,
        content_agreement_ratio=0.5,
        metadata_agreement_ratio=1.0,
        total_files_evaluated=2,
        added_files_count=0,
        removed_files_count=0,
        modified_content_count=1,
        modified_metadata_count=0,
        findings=[
            DiffFindingItem(
                file_path="lib/site-packages/urllib3/util.py",
                diff_type="MODIFIED_CONTENT",
                strict_match=False,
                normalized_match=False,
                size_delta_bytes=50,
                details={},
            )
        ],
    )
    ctx = DiagnosticContext(
        comparison_summary=summary,
        build_1_log_text="Collecting urllib3\nInstalling urllib3-1.26.5",
        build_2_log_text="Collecting urllib3\nInstalling urllib3-2.0.0",
        build_system=DetectedBuildSystem(
            system_type="python",
            primary_config_file="pyproject.toml",
            build_files_found=["pyproject.toml"],
            has_lockfile=False,
            suggested_build_command="pip install",
        ),
    )

    rule = RuleDependencyVariation()
    result = rule.evaluate(ctx)

    assert result is not None
    assert result.category == DiagnosticCategory.DEPENDENCY_VARIATION
    assert result.confidence == ConfidenceLevel.HIGH
    assert "lib/site-packages/urllib3/util.py" in result.affected_paths


def test_rule_toolchain_variation_compiled_outputs():
    """Verify compiler mismatch causing bytecode (.pyc) divergence triggers TOOLCHAIN_VARIATION."""
    summary = ArtifactComparisonSummary(
        artifact_1_path="b1.whl",
        artifact_2_path="b2.whl",
        strict_sha_1="1" * 64,
        strict_sha_2="2" * 64,
        normalized_sha_1="a",
        normalized_sha_2="b",
        exact_match=False,
        normalized_match=False,
        content_agreement_ratio=0.5,
        metadata_agreement_ratio=1.0,
        total_files_evaluated=2,
        added_files_count=0,
        removed_files_count=0,
        modified_content_count=1,
        modified_metadata_count=0,
        findings=[
            DiffFindingItem(
                file_path="app/__pycache__/main.cpython-312.pyc",
                diff_type="MODIFIED_CONTENT",
                strict_match=False,
                normalized_match=False,
                size_delta_bytes=24,
                details={},
            )
        ],
    )
    fp1 = make_sample_fingerprint(python_ver="3.11.2")
    fp2 = make_sample_fingerprint(python_ver="3.12.1")
    ctx = DiagnosticContext(
        comparison_summary=summary,
        env_fingerprint_1=fp1,
        env_fingerprint_2=fp2,
    )

    rule = RuleToolchainVariation()
    result = rule.evaluate(ctx)

    assert result is not None
    assert result.category == DiagnosticCategory.TOOLCHAIN_VARIATION
    assert result.confidence == ConfidenceLevel.HIGH
    assert "app/__pycache__/main.cpython-312.pyc" in result.affected_paths


def test_rule_filesystem_ordering_detection():
    """Verify pure entry ordering differences trigger FILESYSTEM_ORDERING."""
    summary = ArtifactComparisonSummary(
        artifact_1_path="b1.tar",
        artifact_2_path="b2.tar",
        strict_sha_1="1" * 64,
        strict_sha_2="2" * 64,
        normalized_sha_1="same",
        normalized_sha_2="same",
        exact_match=False,
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
    ctx = DiagnosticContext(comparison_summary=summary)
    rule = RuleFilesystemOrdering()
    result = rule.evaluate(ctx)

    assert result is not None
    assert result.category == DiagnosticCategory.FILESYSTEM_ORDERING
    assert result.confidence == ConfidenceLevel.HIGH


def test_rule_metadata_permission_variation():
    """Verify umask/mode differences trigger METADATA_VARIATION."""
    summary = ArtifactComparisonSummary(
        artifact_1_path="b1.tar",
        artifact_2_path="b2.tar",
        strict_sha_1="1" * 64,
        strict_sha_2="2" * 64,
        normalized_sha_1="same",
        normalized_sha_2="same",
        exact_match=False,
        normalized_match=True,
        content_agreement_ratio=1.0,
        metadata_agreement_ratio=0.5,
        total_files_evaluated=2,
        added_files_count=0,
        removed_files_count=0,
        modified_content_count=0,
        modified_metadata_count=1,
        findings=[
            DiffFindingItem(
                file_path="script.sh",
                diff_type="METADATA_ONLY",
                strict_match=False,
                normalized_match=True,
                size_delta_bytes=0,
                details={"mode_1": "0644", "mode_2": "0755"},
            )
        ],
    )
    ctx = DiagnosticContext(comparison_summary=summary)
    rule = RuleMetadataVariation()
    result = rule.evaluate(ctx)

    assert result is not None
    assert result.category == DiagnosticCategory.METADATA_VARIATION
    assert result.confidence == ConfidenceLevel.HIGH
    assert "script.sh" in result.affected_paths


def test_rule_platform_variation():
    """Verify architecture divergence triggers PLATFORM_VARIATION."""
    summary = ArtifactComparisonSummary(
        artifact_1_path="b1.whl",
        artifact_2_path="b2.whl",
        strict_sha_1="1" * 64,
        strict_sha_2="2" * 64,
        normalized_sha_1="a",
        normalized_sha_2="b",
        exact_match=False,
        normalized_match=False,
        content_agreement_ratio=0.0,
        metadata_agreement_ratio=0.0,
        total_files_evaluated=1,
        added_files_count=0,
        removed_files_count=0,
        modified_content_count=1,
        modified_metadata_count=0,
        findings=[
            DiffFindingItem(
                file_path="app/core.cpython-312-x86_64-linux-gnu.so",
                diff_type="MODIFIED_CONTENT",
                strict_match=False,
                normalized_match=False,
                size_delta_bytes=1000,
                details={},
            )
        ],
    )
    fp1 = make_sample_fingerprint(arch="x86_64")
    fp2 = make_sample_fingerprint(arch="aarch64")
    ctx = DiagnosticContext(
        comparison_summary=summary,
        env_fingerprint_1=fp1,
        env_fingerprint_2=fp2,
    )

    rule = RulePlatformVariation()
    result = rule.evaluate(ctx)

    assert result is not None
    assert result.category == DiagnosticCategory.PLATFORM_VARIATION
    assert result.confidence == ConfidenceLevel.HIGH


def test_rule_generated_content_token():
    """Verify random UUID token in diff triggers GENERATED_CONTENT."""
    summary = ArtifactComparisonSummary(
        artifact_1_path="b1.zip",
        artifact_2_path="b2.zip",
        strict_sha_1="1" * 64,
        strict_sha_2="2" * 64,
        normalized_sha_1="a",
        normalized_sha_2="b",
        exact_match=False,
        normalized_match=False,
        content_agreement_ratio=0.0,
        metadata_agreement_ratio=1.0,
        total_files_evaluated=1,
        added_files_count=0,
        removed_files_count=0,
        modified_content_count=1,
        modified_metadata_count=0,
        findings=[
            DiffFindingItem(
                file_path="build_meta.json",
                diff_type="MODIFIED_CONTENT",
                strict_match=False,
                normalized_match=False,
                size_delta_bytes=0,
                details={"diff_snippet": 'Generated token: 4f3a2b1c-8e9d-4c3b-a1b2-c3d4e5f6a7b8'},
            )
        ],
    )
    ctx = DiagnosticContext(comparison_summary=summary)
    rule = RuleGeneratedContent()
    result = rule.evaluate(ctx)

    assert result is not None
    assert result.category == DiagnosticCategory.GENERATED_CONTENT
    assert result.confidence == ConfidenceLevel.MEDIUM


def test_rule_unknown_variation_fallback():
    """Verify unmodeled discrepancy falls back to UNKNOWN_VARIATION with INCONCLUSIVE confidence."""
    summary = ArtifactComparisonSummary(
        artifact_1_path="b1.bin",
        artifact_2_path="b2.bin",
        strict_sha_1="1" * 64,
        strict_sha_2="2" * 64,
        normalized_sha_1="1" * 64,
        normalized_sha_2="2" * 64,
        exact_match=False,
        normalized_match=False,
        content_agreement_ratio=0.0,
        metadata_agreement_ratio=1.0,
        total_files_evaluated=1,
        added_files_count=0,
        removed_files_count=0,
        modified_content_count=1,
        modified_metadata_count=0,
        findings=[
            DiffFindingItem(
                file_path="kernel.bin",
                diff_type="MODIFIED_CONTENT",
                strict_match=False,
                normalized_match=False,
                size_delta_bytes=0,
                details={"diff_snippet": "unexplained binary discrepancy"},
            )
        ],
    )
    # Context with no environment deltas or logs
    ctx = DiagnosticContext(comparison_summary=summary)
    engine = DiagnosticEngine()
    findings = engine.diagnose(ctx)

    assert len(findings) == 1
    assert findings[0].category == DiagnosticCategory.UNKNOWN_VARIATION
    assert findings[0].confidence == ConfidenceLevel.INCONCLUSIVE


def test_controlled_perturbation_validation_upgrade():
    """Verify controlled perturbation upgrades status to EXPERIMENTALLY_VALIDATED only when verified and unconfounded."""
    summary = ArtifactComparisonSummary(
        artifact_1_path="b1.tar",
        artifact_2_path="b2.tar",
        strict_sha_1="1" * 64,
        strict_sha_2="2" * 64,
        normalized_sha_1="same",
        normalized_sha_2="same",
        exact_match=False,
        normalized_match=True,
        content_agreement_ratio=1.0,
        metadata_agreement_ratio=0.0,
        total_files_evaluated=1,
        added_files_count=0,
        removed_files_count=0,
        modified_content_count=0,
        modified_metadata_count=1,
        findings=[
            DiffFindingItem(
                file_path="app.py",
                diff_type="METADATA_ONLY",
                strict_match=False,
                normalized_match=True,
                size_delta_bytes=0,
                details={"mtime_1": 100, "mtime_2": 200},
            )
        ],
    )
    # Perturbation metadata with verified intervention, repeatability, and no confounders
    ctx = DiagnosticContext(
        comparison_summary=summary,
        perturbation_metadata={
            "intervention_verified": True,
            "repeatable": True,
            "isolated_variable": "SOURCE_DATE_EPOCH",
        },
    )
    engine = DiagnosticEngine()
    findings = engine.diagnose(ctx)

    assert len(findings) == 1
    assert findings[0].category == DiagnosticCategory.TIMESTAMP_NONDETERMINISM
    # Verify evidence trail records EXPERIMENTALLY_VALIDATED
    trail = findings[0].evidence_trail
    assert any(e.get("status") == "experimentally_validated" for e in trail)


def test_remediation_risk_classification():
    """Verify remediation recommendations follow 3-tier risk classification and policy preconditions."""
    gen = RemediationGenerator()

    # Level 1: Timestamp nondeterminism
    diag_ts = DiagnosticFindingItem(
        category=DiagnosticCategory.TIMESTAMP_NONDETERMINISM,
        confidence=ConfidenceLevel.HIGH,
        affected_paths=["a.tar"],
        root_cause_summary="timestamp drift",
        evidence_trail=[],
        suggested_remediation="SOURCE_DATE_EPOCH",
    )
    recs_ts = gen.generate(diag_ts)
    assert len(recs_ts) > 0
    assert recs_ts[0].risk_level == RemediationRiskLevel.LEVEL_1_LOW
    assert recs_ts[0].approval_required is False
    assert len(recs_ts[0].policy_preconditions) > 0

    # Level 2: Dependency variation
    diag_dep = DiagnosticFindingItem(
        category=DiagnosticCategory.DEPENDENCY_VARIATION,
        confidence=ConfidenceLevel.HIGH,
        affected_paths=["lib/"],
        root_cause_summary="package drift",
        evidence_trail=[],
        suggested_remediation="lockfile",
    )
    recs_dep = gen.generate(diag_dep)
    assert len(recs_dep) > 0
    assert recs_dep[0].risk_level == RemediationRiskLevel.LEVEL_2_MEDIUM
    assert recs_dep[0].approval_required is True

    # Level 3: Unknown variation
    diag_unk = DiagnosticFindingItem(
        category=DiagnosticCategory.UNKNOWN_VARIATION,
        confidence=ConfidenceLevel.INCONCLUSIVE,
        affected_paths=["x.bin"],
        root_cause_summary="unmodeled",
        evidence_trail=[],
        suggested_remediation="manual triage",
    )
    recs_unk = gen.generate(diag_unk)
    assert len(recs_unk) > 0
    assert recs_unk[0].risk_level == RemediationRiskLevel.LEVEL_3_HIGH
    assert recs_unk[0].approval_required is True


def test_rule_base_image_variation_positive_and_negative():
    """Verify §26.5 RuleBaseImageVariation detects unpinned base tags and clears digest-pinned Dockerfiles."""
    summary_oci = ArtifactComparisonSummary(
        artifact_1_path="image1.tar",
        artifact_2_path="image2.tar",
        strict_sha_1="1" * 64,
        strict_sha_2="2" * 64,
        normalized_sha_1="1" * 64,
        normalized_sha_2="2" * 64,
        exact_match=False,
        normalized_match=False,
        content_agreement_ratio=0.4,
        metadata_agreement_ratio=1.0,
        total_files_evaluated=1,
        added_files_count=0,
        removed_files_count=0,
        modified_content_count=1,
        modified_metadata_count=0,
        findings=[
            DiffFindingItem(
                file_path="layer0.tar",
                diff_type="MODIFIED_CONTENT",
                strict_match=False,
                normalized_match=False,
                size_delta_bytes=120,
                details={"docker_oci_image": True},
            )
        ],
    )
    unpinned_ctx = DiagnosticContext(
        comparison_summary=summary_oci,
        build_system=DetectedBuildSystem(
            system_type="docker",
            primary_config_file="Dockerfile",
            build_files_found=["Dockerfile"],
            has_lockfile=False,
            base_images=["python:3.12-slim"],
            suggested_build_command="docker build .",
        ),
    )
    rule = RuleBaseImageVariation()
    pos_res = rule.evaluate(unpinned_ctx)
    assert pos_res is not None
    assert pos_res.category == DiagnosticCategory.BASE_IMAGE_VARIATION
    assert pos_res.confidence == ConfidenceLevel.MEDIUM

    pinned_ctx = DiagnosticContext(
        comparison_summary=summary_oci,
        build_system=DetectedBuildSystem(
            system_type="docker",
            primary_config_file="Dockerfile",
            build_files_found=["Dockerfile"],
            has_lockfile=True,
            base_images=["python:3.12-slim@sha256:" + "a" * 64],
            suggested_build_command="docker build .",
        ),
    )
    assert rule.evaluate(pinned_ctx) is None


def test_rule_external_input_variation_positive_and_negative():
    """Verify §26.5 RuleExternalInputVariation flags external downloads in build logs and skips clean offline logs."""
    summary = ArtifactComparisonSummary(
        artifact_1_path="out1.tar",
        artifact_2_path="out2.tar",
        strict_sha_1="1" * 64,
        strict_sha_2="2" * 64,
        normalized_sha_1="1" * 64,
        normalized_sha_2="2" * 64,
        exact_match=False,
        normalized_match=False,
        content_agreement_ratio=0.0,
        metadata_agreement_ratio=1.0,
        total_files_evaluated=1,
        added_files_count=0,
        removed_files_count=0,
        modified_content_count=1,
        modified_metadata_count=0,
        findings=[],
    )
    rule = RuleExternalInputVariation()

    net_ctx = DiagnosticContext(
        comparison_summary=summary,
        build_1_log_text="Step 2: curl -fsSL https://example.com/assets.json -o assets.json",
        build_2_log_text="Step 2: curl -fsSL https://example.com/assets.json -o assets.json",
    )
    res = rule.evaluate(net_ctx)
    assert res is not None
    assert res.category == DiagnosticCategory.EXTERNAL_INPUT_VARIATION

    clean_ctx = DiagnosticContext(
        comparison_summary=summary,
        build_1_log_text="Compiling local sources offline...",
        build_2_log_text="Compiling local sources offline...",
    )
    assert rule.evaluate(clean_ctx) is None

