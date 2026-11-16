"""ReproForge Recommendation-Only Remediation Engine.

Generates structured, policy-validated remediation guidance across three risk
levels without autonomously modifying user repositories or applying unapproved patches.
"""

import uuid
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field

from backend.app.diagnostics.evidence import DiagnosticContext
from backend.app.schemas.entities import DiagnosticCategory, DiagnosticFindingItem


class RemediationRiskLevel(str, Enum):
    """3-Tier Remediation Risk Level Classification."""

    LEVEL_1_LOW = "level_1_low"
    # Safe, deterministic, non-semantic adjustment. Conditional on validated build policy.

    LEVEL_2_MEDIUM = "level_2_medium"
    # Semantic or dependency adjustment requiring explicit user review and approval.

    LEVEL_3_HIGH = "level_3_high"
    # Complex, architectural, or unmodeled variation requiring manual investigation.


class RemediationRecommendation(BaseModel):
    """Actionable remediation recommendation with explicit risk levels and policy preconditions."""

    recommendation_id: str = Field(default_factory=lambda: f"rec-{uuid.uuid4().hex[:8]}")
    diagnostic_category: DiagnosticCategory
    title: str
    description: str
    proposed_action: str
    code_snippet: Optional[str] = None
    risk_level: RemediationRiskLevel
    approval_required: bool
    policy_preconditions: List[str] = Field(default_factory=list)
    expected_benefit: str
    compatibility_risk: str
    verification_procedure: str


class RemediationGenerator:
    """Generates policy-conditioned remediation recommendations for diagnosed discrepancies."""

    def generate(
        self,
        diagnostic: DiagnosticFindingItem,
        context: Optional[DiagnosticContext] = None,
    ) -> List[RemediationRecommendation]:
        """Generate structured recommendations grounded in diagnostic findings and build context."""
        recommendations: List[RemediationRecommendation] = []
        cat = diagnostic.category

        # 1. TIMESTAMP NONDETERMINISM
        if cat == DiagnosticCategory.TIMESTAMP_NONDETERMINISM:
            # Policy precondition: Repository must have Git commit history or explicit build epoch
            recommendations.append(
                RemediationRecommendation(
                    diagnostic_category=cat,
                    title="Enforce Deterministic Build Timestamps (SOURCE_DATE_EPOCH)",
                    description="Replace current wall-clock system time with a deterministic UNIX epoch in compilers and archive generators.",
                    proposed_action="Set SOURCE_DATE_EPOCH to the committer timestamp of the current Git revision in build environment.",
                    code_snippet='export SOURCE_DATE_EPOCH=$(git log -1 --pretty=%ct)\nexport PYTHONHASHSEED=0',
                    risk_level=RemediationRiskLevel.LEVEL_1_LOW,
                    approval_required=False,
                    policy_preconditions=[
                        "Valid Git repository with commit history",
                        "Build tools support SOURCE_DATE_EPOCH specification",
                    ],
                    expected_benefit="Eliminates timestamp drift in archive headers, zip metadata, and compiler banners.",
                    compatibility_risk="Low. Standardized by reproducible-builds.org across modern compilers and archive utilities.",
                    verification_procedure="Rebuild twice with a simulated wall-clock delay; verify archive strict SHA-256 match.",
                )
            )

        # 2. FILESYSTEM ORDERING
        elif cat == DiagnosticCategory.FILESYSTEM_ORDERING:
            recommendations.append(
                RemediationRecommendation(
                    diagnostic_category=cat,
                    title="Enforce Lexicographical Archive Member Sorting",
                    description="Ensure directory entries are sorted by path before packing into TAR or ZIP archives.",
                    proposed_action="Add '--sort=name' flag to tar command or pipe sorted file paths into archive tool.",
                    code_snippet='tar --sort=name --owner=root:0 --group=root:0 -czf dist.tar.gz ./app',
                    risk_level=RemediationRiskLevel.LEVEL_1_LOW,
                    approval_required=False,
                    policy_preconditions=["GNU tar or compatible archiver supporting --sort flag"],
                    expected_benefit="Guarantees identical entry ordering regardless of underlying filesystem readdir() sequence.",
                    compatibility_risk="Low. Does not alter file contents.",
                    verification_procedure="Rebuild on filesystems with different inode allocations; verify raw archive hash matches.",
                )
            )

        # 3. METADATA VARIATION
        elif cat == DiagnosticCategory.METADATA_VARIATION:
            recommendations.append(
                RemediationRecommendation(
                    diagnostic_category=cat,
                    title="Standardize POSIX Permissions and Build Umask",
                    description="Enforce standardized file modes and root ownership attributes on archived files.",
                    proposed_action="Set umask 0022 and clamp archive permissions (0644 for regular files, 0755 for executables).",
                    code_snippet='umask 0022\ntar --mode="go=rX,u+rw" --owner=root:0 --group=root:0 -cf bundle.tar ./src',
                    risk_level=RemediationRiskLevel.LEVEL_1_LOW,
                    approval_required=False,
                    policy_preconditions=["POSIX filesystem target"],
                    expected_benefit="Removes differences caused by differing host user accounts or developer umask settings.",
                    compatibility_risk="Low. Files remain readable by all standard runtimes.",
                    verification_procedure="Build under two different user accounts; verify identical permission bits.",
                )
            )

        # 4. DEPENDENCY VARIATION
        elif cat == DiagnosticCategory.DEPENDENCY_VARIATION:
            recommendations.append(
                RemediationRecommendation(
                    diagnostic_category=cat,
                    title="Pin Transitive Dependencies with Cryptographic Hashes",
                    description="Generate and commit an immutable lockfile recording exact resolved package versions and SHA-256 digests.",
                    proposed_action="Use pip-compile --generate-hashes (Python) or npm ci with package-lock.json (Node.js).",
                    code_snippet='pip-compile --generate-hashes requirements.in -o requirements.txt',
                    risk_level=RemediationRiskLevel.LEVEL_2_MEDIUM,
                    approval_required=True,
                    policy_preconditions=[
                        "Requires explicit developer review to ensure upgraded dependencies do not introduce breaking API changes",
                    ],
                    expected_benefit="Prevents upstream package releases from silently altering build outputs.",
                    compatibility_risk="Medium. Version pinning requires deliberate ongoing maintenance and security update workflows.",
                    verification_procedure="Build on two independent machines with cleared package caches; verify identical package resolutions.",
                )
            )

        # 5. BASE IMAGE VARIATION
        elif cat == DiagnosticCategory.BASE_IMAGE_VARIATION:
            recommendations.append(
                RemediationRecommendation(
                    diagnostic_category=cat,
                    title="Pin Container Base Images to Immutable Digests",
                    description="Reference base images by cryptographic content digest (@sha256:...) rather than mutable tags (:latest).",
                    proposed_action="Resolve current base image digest via 'docker inspect' and pin in Dockerfile FROM instruction.",
                    code_snippet='FROM python:3.12-slim@sha256:3a6f1e8e... (pinned digest)',
                    risk_level=RemediationRiskLevel.LEVEL_2_MEDIUM,
                    approval_required=True,
                    policy_preconditions=["Docker/OCI containerized application build"],
                    expected_benefit="Guarantees base image layers and system libraries remain byte-for-byte identical over space and time.",
                    compatibility_risk="Medium. Base image security patches must be explicitly updated by refreshing the digest.",
                    verification_procedure="Pull base image on fresh host; verify container layer digests match exactly.",
                )
            )

        # 6. TOOLCHAIN VARIATION
        elif cat == DiagnosticCategory.TOOLCHAIN_VARIATION:
            recommendations.append(
                RemediationRecommendation(
                    diagnostic_category=cat,
                    title="Standardize Build Compiler and Runtime Toolchains",
                    description="Execute builds inside a hermetic container environment with fixed compiler and runtime versions.",
                    proposed_action="Pin compiler version in Dockerfile or CI pipeline runner definition.",
                    code_snippet='FROM gcc:12.3.0 (pinned compiler toolchain container)',
                    risk_level=RemediationRiskLevel.LEVEL_2_MEDIUM,
                    approval_required=True,
                    policy_preconditions=["Containerized build infrastructure available"],
                    expected_benefit="Eliminates bytecode magic number deltas and compiler optimization differences.",
                    compatibility_risk="Low to Medium depending on cross-platform requirements.",
                    verification_procedure="Inspect 'python --version' or 'gcc --version' in build environment fingerprint.",
                )
            )

        # 7. ENVIRONMENT LEAKAGE
        elif cat == DiagnosticCategory.ENVIRONMENT_LEAKAGE:
            recommendations.append(
                RemediationRecommendation(
                    diagnostic_category=cat,
                    title="Sanitize Build-Time Environment Variables",
                    description="Prevent host-specific environment variables ($HOSTNAME, $USER, local paths) from leaking into outputs.",
                    proposed_action="Execute build commands with an explicit environment allowlist or 'env -i' clean environment.",
                    code_snippet='env -i PATH="$PATH" LC_ALL=C.UTF-8 SOURCE_DATE_EPOCH="$SOURCE_DATE_EPOCH" make build',
                    risk_level=RemediationRiskLevel.LEVEL_2_MEDIUM,
                    approval_required=True,
                    policy_preconditions=["Build script must declare all required environment variables"],
                    expected_benefit="Removes machine-specific strings and paths from compiled headers and configurations.",
                    compatibility_risk="Medium. Incomplete allowlist can cause build scripts that rely on host variables to fail.",
                    verification_procedure="Build under two different hostnames; verify strings are absent in artifact binary diff.",
                )
            )

        # 8. PLATFORM, GENERATED CONTENT, EXTERNAL INPUT, UNKNOWN
        else:
            recommendations.append(
                RemediationRecommendation(
                    diagnostic_category=cat,
                    title="Perform In-Depth Manual Investigation",
                    description="The observed discrepancy requires developer code review or controlled sensitivity analysis.",
                    proposed_action="Inspect the artifact diff findings with 'reproforge compare' or perform single-variable perturbation.",
                    code_snippet=None,
                    risk_level=RemediationRiskLevel.LEVEL_3_HIGH,
                    approval_required=True,
                    policy_preconditions=["Manual developer triage"],
                    expected_benefit="Identifies architecture-specific outputs, dynamic randomness, or un-isolated network dependencies.",
                    compatibility_risk="High. Automated repair is unsafe without domain-specific application knowledge.",
                    verification_procedure="Re-run comparison under identical controlled environment conditions.",
                )
            )

        return recommendations
