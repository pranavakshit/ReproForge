"""Repository for BuildExecution entity and durable startup recovery."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.models.entities import BuildExecutionModel
from backend.app.repositories.base import BaseRepository
from backend.app.schemas.entities import BuildStatus


class BuildRepository(BaseRepository[BuildExecutionModel]):
    """Async repository for BuildExecution entities and crash recovery reconciliation."""

    def __init__(self, session: AsyncSession):
        super().__init__(BuildExecutionModel, session)

    async def create_build(
        self,
        repository_id: str,
        revision_id: str,
        build_id: Optional[str] = None,
        profile_name: str = "default-isolated",
        fingerprint_id: Optional[str] = None,
    ) -> BuildExecutionModel:
        """Create and persist a new build execution record in QUEUED status."""
        now = datetime.now(timezone.utc)
        build_obj = BuildExecutionModel(
            repository_id=repository_id,
            revision_id=revision_id,
            fingerprint_id=fingerprint_id,
            profile_name=profile_name,
            status=BuildStatus.QUEUED.value,
            started_at=now,
        )
        if build_id:
            build_obj.id = build_id

        return await self.add(build_obj)

    async def update_status(
        self,
        build_id: str,
        status: BuildStatus,
        exit_code: Optional[int] = None,
        duration_seconds: Optional[float] = None,
        log_file_path: Optional[str] = None,
    ) -> Optional[BuildExecutionModel]:
        """Update status, exit code, duration, and log path for a build execution."""
        build = await self.get_by_id(build_id)
        if not build:
            return None

        build.status = status.value if isinstance(status, BuildStatus) else status
        if exit_code is not None:
            build.exit_code = exit_code
        if duration_seconds is not None:
            build.duration_seconds = duration_seconds
        if log_file_path:
            build.log_file_path = log_file_path

        if status in (BuildStatus.COMPLETED, BuildStatus.FAILED, BuildStatus.TIMED_OUT, BuildStatus.CANCELLED, BuildStatus.EXECUTION_INTERRUPTED):
            build.completed_at = datetime.now(timezone.utc)

        await self.session.flush()
        return build

    async def cancel_build(self, build_id: str) -> Optional[BuildExecutionModel]:
        """Mark a specific active build as CANCELLED."""
        return await self.update_status(
            build_id=build_id,
            status=BuildStatus.CANCELLED,
            exit_code=-1,
        )

    async def list_by_repository(
        self, repository_id: str, limit: int = 50, offset: int = 0
    ) -> List[BuildExecutionModel]:
        """List builds associated with a specific repository."""
        stmt = (
            select(BuildExecutionModel)
            .where(BuildExecutionModel.repository_id == repository_id)
            .order_by(BuildExecutionModel.started_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def reconcile_interrupted_builds(self) -> List[str]:
        """Durable recovery reconciliation routine executed during engine startup.
        
        Scans for builds in non-terminal in-flight states (QUEUED, BUILDING, PROVISIONING,
        COLLECTING, FINGERPRINTING) that were orphaned by an unexpected process crash.
        Transitions them to EXECUTION_INTERRUPTED and records completed_at timestamp.
        """
        active_statuses = [
            BuildStatus.QUEUED.value,
            BuildStatus.BUILDING.value,
            BuildStatus.PROVISIONING.value,
            BuildStatus.COLLECTING.value,
            BuildStatus.FINGERPRINTING.value,
        ]
        stmt = select(BuildExecutionModel).where(BuildExecutionModel.status.in_(active_statuses))
        result = await self.session.execute(stmt)
        lingering = list(result.scalars().all())

        reconciled_ids: List[str] = []
        now = datetime.now(timezone.utc)

        for b in lingering:
            b.status = BuildStatus.EXECUTION_INTERRUPTED.value
            b.completed_at = now
            reconciled_ids.append(b.id)

        if reconciled_ids:
            await self.session.flush()

        return reconciled_ids
