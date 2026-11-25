"""Shared application service for Artifact Comparison, Diagnostics, Scoring, and Persistence."""

from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.comparison.diff import ArtifactComparisonSummary, StructuralDiffEngine
from backend.app.core.config import settings
from backend.app.database.session import AsyncSessionLocal
from backend.app.diagnostics.engine import DiagnosticEngine
from backend.app.diagnostics.evidence import DiagnosticContext
from backend.app.diagnostics.remediation import RemediationGenerator, RemediationRecommendation
from backend.app.models.entities import (
    BuildExecutionModel,
    EnvironmentFingerprintModel,
    RepositoryModel,
)
from backend.app.orchestration.detector import BuildConfigurationDetector, DetectedBuildSystem
from backend.app.repositories.comparison_repo import ComparisonRepository
from backend.app.repositories.diagnostic_repo import DiagnosticRepository
from backend.app.schemas.entities import (
    DiagnosticFindingItem,
    EnvironmentFingerprintRead,
    LocaleInfo,
    SystemInfo,
    ToolchainInfo,
)
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

    @staticmethod
    def _fp_model_to_schema(fp: Optional[EnvironmentFingerprintModel]) -> Optional[EnvironmentFingerprintRead]:
        if not fp:
            return None
        return EnvironmentFingerprintRead(
            id=fp.id,
            fingerprint_hash=fp.fingerprint_hash,
            system_info=SystemInfo(**fp.system_info),
            toolchain_info=ToolchainInfo(**fp.toolchain_info),
            locale_info=LocaleInfo(**fp.locale_info),
            environment_variables=fp.environment_variables or {},
            container_runtime_info=fp.container_runtime_info or {},
            captured_at=fp.captured_at,
        )

    @staticmethod
    def _read_build_log(log_path: Optional[str]) -> Optional[str]:
        if not log_path:
            return None
        p = Path(log_path)
        if not p.is_file():
            return None
        try:
            return p.read_text(encoding="utf-8", errors="replace")[:200_000]
        except Exception:
            return None

    async def _build_diagnostic_context(
        self,
        session: Optional[AsyncSession],
        summary: ArtifactComparisonSummary,
        build_id_1: Optional[str],
        build_id_2: Optional[str],
        perturbation_metadata: Optional[Dict[str, Any]] = None,
    ) -> DiagnosticContext:
        fp1 = fp2 = None
        log1 = log2 = None
        build_system: Optional[DetectedBuildSystem] = None

        if session and build_id_1 and build_id_2:
            b1 = (await session.execute(select(BuildExecutionModel).where(BuildExecutionModel.id == build_id_1))).scalars().first()
            b2 = (await session.execute(select(BuildExecutionModel).where(BuildExecutionModel.id == build_id_2))).scalars().first()

            if b1:
                log1 = self._read_build_log(b1.log_file_path)
                if b1.fingerprint_id:
                    fp1_m = (await session.execute(select(EnvironmentFingerprintModel).where(EnvironmentFingerprintModel.id == b1.fingerprint_id))).scalars().first()
                    fp1 = self._fp_model_to_schema(fp1_m)
                repo_m = (await session.execute(select(RepositoryModel).where(RepositoryModel.id == b1.repository_id))).scalars().first()
                if repo_m and Path(repo_m.repo_url).is_dir():
                    detected = BuildConfigurationDetector().detect(Path(repo_m.repo_url))
                    non_generic = [d for d in detected if d.system_type != "generic"]
                    build_system = non_generic[0] if non_generic else (detected[0] if detected else None)

            if b2:
                log2 = self._read_build_log(b2.log_file_path)
                if b2.fingerprint_id:
                    fp2_m = (await session.execute(select(EnvironmentFingerprintModel).where(EnvironmentFingerprintModel.id == b2.fingerprint_id))).scalars().first()
                    fp2 = self._fp_model_to_schema(fp2_m)

        return DiagnosticContext(
            comparison_summary=summary,
            env_fingerprint_1=fp1,
            env_fingerprint_2=fp2,
            build_system=build_system,
            build_1_log_text=log1,
            build_2_log_text=log2,
            perturbation_metadata=perturbation_metadata,
        )

    async def compare_artifacts(
        self,
        artifact1: Path,
        artifact2: Path,
        build_id_1: Optional[str] = None,
        build_id_2: Optional[str] = None,
        work_dir: Optional[Path] = None,
        perturbation_metadata: Optional[Dict[str, Any]] = None,
    ) -> ComparisonServiceOutcome:
        """Execute full differential comparison, diagnostic correlation, scoring, and persistence."""
        temp_work = work_dir or (settings.DATA_DIR / "temp_comparison")
        temp_work.mkdir(parents=True, exist_ok=True)

        # 1. Structural differential analysis (Mode A vs Mode B)
        summary = self.diff_engine.compare_artifacts(artifact1, artifact2, temp_work)

        # 2. Hydrate full diagnostic context (fingerprints, logs, build system, perturbations)
        if self._session:
            ctx = await self._build_diagnostic_context(self._session, summary, build_id_1, build_id_2, perturbation_metadata)
        elif build_id_1 and build_id_2:
            async with AsyncSessionLocal() as session:
                ctx = await self._build_diagnostic_context(session, summary, build_id_1, build_id_2, perturbation_metadata)
        else:
            ctx = DiagnosticContext(comparison_summary=summary, perturbation_metadata=perturbation_metadata)

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
