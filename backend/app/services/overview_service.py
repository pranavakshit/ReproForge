"""Shared application service for computing aggregate overview metrics and system telemetry."""

from typing import Any, Dict, List, Optional
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.database.session import AsyncSessionLocal
from backend.app.models.entities import (
    BuildComparisonModel,
    BuildExecutionModel,
    RepositoryModel,
    ReproducibilityScoreModel,
    SourceRevisionModel,
)
from backend.app.services.fingerprint_service import FingerprintService


class OverviewService:
    """Aggregates system-wide reproducibility statistics and environment telemetry."""

    def __init__(
        self,
        session: Optional[AsyncSession] = None,
        fingerprint_service: Optional[FingerprintService] = None,
    ) -> None:
        self._session = session
        self.fingerprint_service = fingerprint_service or FingerprintService()

    async def get_overview_metrics(self) -> Dict[str, Any]:
        """Compute aggregate dashboard metrics from SQLite database."""
        if self._session:
            return await self._compute_metrics(self._session)
        async with AsyncSessionLocal() as session:
            return await self._compute_metrics(session)

    async def _compute_metrics(self, session: AsyncSession) -> Dict[str, Any]:
        # 1. Total build executions count
        total_builds_stmt = select(func.count(BuildExecutionModel.id))
        total_builds_res = await session.execute(total_builds_stmt)
        total_builds = total_builds_res.scalar() or 0

        # 2. Query reproducibility scores
        score_stmt = select(ReproducibilityScoreModel)
        score_res = await session.execute(score_stmt)
        scores = score_res.scalars().all()

        total_comparisons = len(scores)
        exact_matches = sum(1 for s in scores if s.exact_match)
        normalized_matches = sum(1 for s in scores if s.normalized_match)
        inconclusive_count = sum(1 for s in scores if s.verdict == "inconclusive")

        # Verdict counts breakdown
        verdict_counts: Dict[str, int] = {
            "bit_for_bit_reproducible": 0,
            "normalized_reproducible": 0,
            "partially_reproducible": 0,
            "non_reproducible": 0,
            "inconclusive": 0,
        }
        for s in scores:
            v = s.verdict.lower()
            if v in verdict_counts:
                verdict_counts[v] += 1
            else:
                verdict_counts[v] = 1

        # Calculate rates
        if total_comparisons > 0:
            exact_rate = round((exact_matches / total_comparisons) * 100, 1)
            norm_rate = round((normalized_matches / total_comparisons) * 100, 1)
        else:
            exact_rate = 0.0
            norm_rate = 0.0

        # 3. Recent 8 build executions
        recent_stmt = (
            select(BuildExecutionModel)
            .order_by(BuildExecutionModel.started_at.desc())
            .limit(8)
        )
        recent_res = await session.execute(recent_stmt)
        recent_models = recent_res.scalars().all()

        recent_builds: List[Dict[str, Any]] = []
        for b in recent_models:
            # Resolve repository name
            repo_name = "unknown"
            if b.repository_id:
                rname = (
                    await session.execute(
                        select(RepositoryModel.name).where(RepositoryModel.id == b.repository_id)
                    )
                ).scalar()
                if rname:
                    repo_name = rname

            # Get commit sha from revision if loaded
            rev_sha = "HEAD"
            if b.revision_id:
                rev_stmt = select(SourceRevisionModel.commit_sha).where(
                    SourceRevisionModel.id == b.revision_id
                )
                rev_res = await session.execute(rev_stmt)
                rev_sha_val = rev_res.scalar()
                if rev_sha_val:
                    rev_sha = rev_sha_val[:8]

            # Resolve real verdict from reproducibility_scores linked to comparisons if available
            verd_stmt = (
                select(ReproducibilityScoreModel.verdict)
                .join(
                    BuildComparisonModel,
                    BuildComparisonModel.id == ReproducibilityScoreModel.comparison_id,
                )
                .where(
                    (BuildComparisonModel.build_id_1 == b.id)
                    | (BuildComparisonModel.build_id_2 == b.id)
                )
                .limit(1)
            )
            verd_res = await session.execute(verd_stmt)
            real_verdict = verd_res.scalar()

            duration_str = f"{b.duration_seconds:.1f}s" if b.duration_seconds else "-"
            verdict_label = (real_verdict or ("UNCOMPARED" if b.status == "completed" else b.status)).upper()
            recent_builds.append(
                {
                    "build_id": b.id,
                    "repo": repo_name,
                    "revision": rev_sha,
                    "commit_sha": rev_sha,
                    "backend": b.profile_name,
                    "status": b.status,
                    "verdict": verdict_label,
                    "duration": duration_str,
                }
            )

        # 4. Host telemetry (cached toolchain probe keeps overview queries sub-20ms)
        sys_info, tools_info = self.fingerprint_service.get_telemetry(use_cache=True)
        docker_cli = tools_info.tools.get("docker_cli", {}).get("version") or "Not detected on PATH"
        telemetry = {
            "os_name": sys_info.os_name,
            "os_release": sys_info.os_release,
            "cpu_arch": sys_info.cpu_arch,
            "cpu_count": sys_info.cpu_count,
            "ram_gb": round(sys_info.total_ram_bytes / (1024**3), 2),
            "hostname_masked": sys_info.hostname_masked,
            "detected_toolchains": list(tools_info.tools.keys()),
            "docker_cli_version": docker_cli,
            "database_path": "data/reproforge.db",
        }

        return {
            "total_builds": total_builds,
            "total_comparisons": total_comparisons,
            "bit_for_bit_rate": f"{exact_rate}%",
            "normalized_rate": f"{norm_rate}%",
            "unresolved_count": inconclusive_count,
            "verdict_breakdown": verdict_counts,
            "telemetry": telemetry,
            "recent_builds": recent_builds,
        }
