"""Repository for DiagnosticFinding entity persistence."""

from typing import List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.models.entities import DiagnosticFindingModel
from backend.app.repositories.base import BaseRepository
from backend.app.schemas.entities import DiagnosticFindingItem


class DiagnosticRepository(BaseRepository[DiagnosticFindingModel]):
    """Async repository for explainable diagnostic conclusions."""

    def __init__(self, session: AsyncSession):
        super().__init__(DiagnosticFindingModel, session)

    async def record_findings(
        self, comparison_id: str, findings: List[DiagnosticFindingItem]
    ) -> List[DiagnosticFindingModel]:
        """Persist diagnostic findings for a comparison record."""
        records: List[DiagnosticFindingModel] = []
        for f in findings:
            item = DiagnosticFindingModel(
                comparison_id=comparison_id,
                category=f.category.value if hasattr(f.category, "value") else str(f.category),
                confidence=f.confidence.value if hasattr(f.confidence, "value") else str(f.confidence),
                affected_paths=f.affected_paths,
                root_cause_summary=f.root_cause_summary,
                evidence_trail=f.evidence_trail,
                suggested_remediation=f.suggested_remediation,
            )
            self.session.add(item)
            records.append(item)

        await self.session.flush()
        return records

    async def get_by_comparison(self, comparison_id: str) -> List[DiagnosticFindingModel]:
        """Retrieve all diagnostic findings for a given comparison."""
        stmt = select(DiagnosticFindingModel).where(
            DiagnosticFindingModel.comparison_id == comparison_id
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
