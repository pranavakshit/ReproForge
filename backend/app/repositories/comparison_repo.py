"""Repository for BuildComparison, DiffFinding, and ReproducibilityScore entities."""

from typing import List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.comparison.diff import ArtifactComparisonSummary
from backend.app.models.entities import (
    BuildComparisonModel,
    DiffFindingModel,
    ReproducibilityScoreModel,
)
from backend.app.repositories.base import BaseRepository
from backend.app.scoring.calculator import ReproducibilityAssessment


class ComparisonRepository(BaseRepository[BuildComparisonModel]):
    """Async repository for build comparison records, diff findings, and scores."""

    def __init__(self, session: AsyncSession):
        super().__init__(BuildComparisonModel, session)

    async def record_comparison(
        self,
        build_id_1: str,
        build_id_2: str,
        summary: ArtifactComparisonSummary,
        assessment: Optional[ReproducibilityAssessment] = None,
        mode: str = "strict",
    ) -> BuildComparisonModel:
        """Persist a pairwise comparison outcome, its findings, and optional reproducibility assessment."""
        comp = BuildComparisonModel(
            build_id_1=build_id_1,
            build_id_2=build_id_2,
            mode=mode,
            exact_match=summary.exact_match,
            normalized_match=summary.normalized_match,
        )
        self.session.add(comp)
        await self.session.flush()

        # Record diff findings
        for f in summary.findings:
            finding = DiffFindingModel(
                comparison_id=comp.id,
                file_path=f.file_path,
                diff_type=f.diff_type,
                strict_match=f.strict_match,
                normalized_match=f.normalized_match,
                size_delta_bytes=f.size_delta_bytes,
                details=f.details,
            )
            self.session.add(finding)

        # Record score if present
        if assessment:
            score_model = ReproducibilityScoreModel(
                comparison_id=comp.id,
                exact_match=assessment.exact_match,
                normalized_match=assessment.normalized_match,
                content_agreement_ratio=assessment.content_agreement_ratio,
                metadata_agreement_ratio=assessment.metadata_agreement_ratio,
                environment_consistency_score=assessment.environment_consistency_score,
                composite_reproducibility_index=assessment.composite_reproducibility_index,
                verdict=assessment.verdict.value,
                unexplained_differences_count=assessment.unexplained_differences_count,
            )
            self.session.add(score_model)

        await self.session.flush()
        return comp

    async def get_with_findings(self, comparison_id: str) -> Optional[BuildComparisonModel]:
        """Retrieve comparison along with related diff findings."""
        return await self.get_by_id(comparison_id)
