"""Reproducibility Assessment and Transparent Scoring Engine.

Computes the primary multi-dimensional reproducibility assessment vector and
categorical verdict, alongside a transparent, secondary Composite Reproducibility
Index (CRI) for quantitative research benchmarking.
"""

from typing import List, Optional
from pydantic import BaseModel, Field

from backend.app.comparison.diff import ArtifactComparisonSummary
from backend.app.diagnostics.evidence import DiagnosticContext
from backend.app.schemas.entities import (
    ConfidenceLevel,
    DiagnosticCategory,
    DiagnosticFindingItem,
    ReproducibilityScoreRead,
    ReproducibilityVerdict,
)


class ReproducibilityAssessment(BaseModel):
    """Full reproducibility assessment containing primary vector, verdict, and secondary CRI."""

    # --- PRIMARY RESULTS (Dimensional Vector & Categorical Verdict) ---
    verdict: ReproducibilityVerdict
    exact_match: bool
    normalized_match: bool
    content_agreement_ratio: float
    metadata_agreement_ratio: float
    environment_consistency_score: float
    unexplained_differences_count: int
    total_files_evaluated: int

    # --- SECONDARY BENCHMARK SCALAR ---
    composite_reproducibility_index: float
    cri_weights: dict = Field(
        default_factory=lambda: {
            "w_exact": 0.40,
            "w_norm": 0.30,
            "w_content": 0.20,
            "w_meta": 0.10,
        }
    )
    cri_disclaimer: str = (
        "CRI is a secondary research benchmark scalar and must not be used as an absolute quality metric. "
        "The primary assessment is the categorical verdict and multi-dimensional metric vector."
    )

    def to_schema(self) -> ReproducibilityScoreRead:
        """Export to domain API schema."""
        return ReproducibilityScoreRead(
            exact_match=self.exact_match,
            normalized_match=self.normalized_match,
            content_agreement_ratio=self.content_agreement_ratio,
            metadata_agreement_ratio=self.metadata_agreement_ratio,
            environment_consistency_score=self.environment_consistency_score,
            composite_reproducibility_index=self.composite_reproducibility_index,
            verdict=self.verdict,
            unexplained_differences_count=self.unexplained_differences_count,
        )


class ReproducibilityCalculator:
    """Calculates multi-dimensional reproducibility metrics and determines categorical verdicts."""

    @classmethod
    def calculate_environment_consistency(cls, context: DiagnosticContext) -> float:
        """Calculate environment consistency ratio between build 1 and build 2 (0.0 to 1.0)."""
        if not context.env_fingerprint_1 or not context.env_fingerprint_2:
            return 1.0

        fp1 = context.env_fingerprint_1
        fp2 = context.env_fingerprint_2

        # 1. Hardware/OS match (weight 0.40)
        sys_match = 1.0 if (
            fp1.system_info.os_name == fp2.system_info.os_name
            and fp1.system_info.cpu_arch == fp2.system_info.cpu_arch
        ) else 0.0

        # 2. Toolchain match (weight 0.40)
        tools1 = fp1.toolchain_info.tools
        tools2 = fp2.toolchain_info.tools
        common_tools = set(tools1.keys()) & set(tools2.keys())
        matching_tools = sum(
            1 for t in common_tools
            if tools1[t].get("version") == tools2[t].get("version")
        )
        total_unique_tools = len(set(tools1.keys()) | set(tools2.keys()))
        tool_score = (matching_tools / total_unique_tools) if total_unique_tools > 0 else 1.0

        # 3. Environment variables match (weight 0.20)
        env1 = fp1.environment_variables
        env2 = fp2.environment_variables
        all_keys = set(env1.keys()) | set(env2.keys())
        matching_env = sum(1 for k in all_keys if env1.get(k) == env2.get(k))
        env_score = (matching_env / len(all_keys)) if all_keys else 1.0

        consistency = (0.40 * sys_match) + (0.40 * tool_score) + (0.20 * env_score)
        return round(consistency, 4)

    @classmethod
    def determine_verdict(
        cls,
        exact_match: bool,
        normalized_match: bool,
        content_ratio: float,
        diagnostics: List[DiagnosticFindingItem],
    ) -> ReproducibilityVerdict:
        """Determine unambiguous categorical verdict from primary observations."""
        if exact_match:
            return ReproducibilityVerdict.BIT_FOR_BIT_REPRODUCIBLE
        elif normalized_match:
            return ReproducibilityVerdict.NORMALIZED_REPRODUCIBLE
        elif content_ratio > 0.0:
            return ReproducibilityVerdict.PARTIALLY_REPRODUCIBLE
        elif any(d.category == DiagnosticCategory.UNKNOWN_VARIATION and d.confidence == ConfidenceLevel.INCONCLUSIVE for d in diagnostics):
            return ReproducibilityVerdict.INCONCLUSIVE
        else:
            return ReproducibilityVerdict.NON_REPRODUCIBLE

    @classmethod
    def evaluate(
        cls,
        summary: ArtifactComparisonSummary,
        diagnostics: List[DiagnosticFindingItem],
        context: Optional[DiagnosticContext] = None,
    ) -> ReproducibilityAssessment:
        """Execute full reproducibility assessment and CRI computation."""
        s_exact = 1.0 if summary.exact_match else 0.0
        s_norm = 1.0 if summary.normalized_match else 0.0
        r_content = summary.content_agreement_ratio
        r_meta = summary.metadata_agreement_ratio

        env_consistency = cls.calculate_environment_consistency(context) if context else 1.0

        # Count unexplained differences (findings categorized as UNKNOWN_VARIATION)
        unexplained = sum(
            len(d.affected_paths) for d in diagnostics
            if d.category == DiagnosticCategory.UNKNOWN_VARIATION
        )

        verdict = cls.determine_verdict(
            exact_match=summary.exact_match,
            normalized_match=summary.normalized_match,
            content_ratio=r_content,
            diagnostics=diagnostics,
        )

        # Compute secondary CRI scalar
        cri = round(
            (0.40 * s_exact) + (0.30 * s_norm) + (0.20 * r_content) + (0.10 * r_meta),
            4,
        )

        return ReproducibilityAssessment(
            verdict=verdict,
            exact_match=summary.exact_match,
            normalized_match=summary.normalized_match,
            content_agreement_ratio=r_content,
            metadata_agreement_ratio=r_meta,
            environment_consistency_score=env_consistency,
            unexplained_differences_count=unexplained,
            total_files_evaluated=summary.total_files_evaluated,
            composite_reproducibility_index=cri,
        )
