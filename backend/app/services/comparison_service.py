"""Shared application service for Artifact Comparison, Diagnostics, Scoring, and Persistence."""

from pathlib import Path
from typing import List, Optional
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.comparison.diff import ArtifactComparisonSummary, StructuralDiffEngine
from backend.app.core.config import settings
from backend.app.database.session import AsyncSessionLocal
from backend.app.diagnostics.engine import DiagnosticEngine
from backend.app.diagnostics.evidence import DiagnosticContext
from backend.app.diagnostics.remediation import RemediationGenerator, RemediationRecommendation
from backend.app.repositories.comparison_repo import ComparisonRepository
from backend.app.repositories.diagnostic_repo import DiagnosticRepository
from backend.app.schemas.entities import DiagnosticFindingItem
from backend.app.scoring.calculator import ReproducibilityAssessment, ReproducibilityCalculator


class ComparisonServiceOutcome(BaseModel):
    """Aggregate result of an artifact differential evaluation and diagnostic correlation."""

    summary: ArtifactComparisonSummary
    diagnostics: List[DiagnosticFindingItem]
    remediations: List[RemediationRecommendation]
    assessment: ReproducibilityAssessment
    comparison_id: Optional[str] = None


class ComparisonService:
    """Coordinates artifact differential analysis, diagnostic rules, scoring, and persistence."""

    def __init__(
        self,
        session: Optional[AsyncSession] = None,
        diff_engine: Optional[StructuralDiffEngine] = None,
        diagnostic_engine: Optional[DiagnosticEngine] = None,
        remediation_generator: Optional[RemediationGenerator] = None,
    ):
        self._session = session
        self.diff_engine = diff_engine or StructuralDiffEngine()
        self.diagnostic_engine = diagnostic_engine or DiagnosticEngine()
        self.remediation_generator = remediation_generator or RemediationGenerator()

    async def compare_artifacts(
        self,
        artifact1: Path,
        artifact2: Path,
        build_id_1: Optional[str] = None,
        build_id_2: Optional[str] = None,
        work_dir: Optional[Path] = None,
    ) -> ComparisonServiceOutcome:
        """Execute full differential comparison, diagnostic correlation, scoring, and persistence."""
        temp_work = work_dir or (settings.DATA_DIR / "temp_comparison")
        temp_work.mkdir(parents=True, exist_ok=True)

        # 1. Structural differential analysis (Mode A vs Mode B)
        summary = self.diff_engine.compare_artifacts(artifact1, artifact2, temp_work)

        # 2. Diagnostic correlation
        ctx = DiagnosticContext(comparison_summary=summary)
        diagnostics = self.diagnostic_engine.diagnose(ctx)

        # 3. Actionable remediation generation
        remediations: List[RemediationRecommendation] = []
        for d in diagnostics:
            remediations.extend(self.remediation_generator.generate(d, ctx))

        # 4. Multi-dimensional reproducibility assessment & CRI calculation
        assessment = ReproducibilityCalculator.evaluate(summary, diagnostics, ctx)

        comparison_id: Optional[str] = None

        # 5. Persist comparison and diagnostic records if build IDs are supplied
        if build_id_1 and build_id_2:
            if self._session:
                comparison_id = await self._persist_comparison(
                    self._session, build_id_1, build_id_2, summary, assessment, diagnostics
                )
            else:
                async with AsyncSessionLocal() as session:
                    async with session.begin():
                        comparison_id = await self._persist_comparison(
                            session, build_id_1, build_id_2, summary, assessment, diagnostics
                        )

        return ComparisonServiceOutcome(
            summary=summary,
            diagnostics=diagnostics,
            remediations=remediations,
            assessment=assessment,
            comparison_id=comparison_id,
        )

    async def _persist_comparison(
        self,
        session: AsyncSession,
        build_id_1: str,
        build_id_2: str,
        summary: ArtifactComparisonSummary,
        assessment: ReproducibilityAssessment,
        diagnostics: List[DiagnosticFindingItem],
    ) -> str:
        comp_repo = ComparisonRepository(session)
        diag_repo = DiagnosticRepository(session)

        comp_model = await comp_repo.record_comparison(
            build_id_1=build_id_1,
            build_id_2=build_id_2,
            summary=summary,
            assessment=assessment,
        )

        await diag_repo.record_findings(comp_model.id, diagnostics)

        return comp_model.id
