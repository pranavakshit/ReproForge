"""Repository for ArtifactItem entity persistence."""

from typing import Any, List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.models.entities import ArtifactItemModel
from backend.app.repositories.base import BaseRepository
from backend.app.schemas.entities import ArtifactItemCreate


class ArtifactRepository(BaseRepository[ArtifactItemModel]):
    """Async repository for collected build artifact records."""

    def __init__(self, session: AsyncSession):
        super().__init__(ArtifactItemModel, session)

    async def record_artifacts(
        self, build_execution_id: str, artifacts: List[ArtifactItemCreate]
    ) -> List[ArtifactItemModel]:
        """Persist a batch of collected artifacts for a build execution."""
        records: List[ArtifactItemModel] = []
        for a in artifacts:
            metadata_dict = a.metadata if hasattr(a, "metadata") and a.metadata else (
                a.metadata_json if hasattr(a, "metadata_json") else {}
            )
            item = ArtifactItemModel(
                build_execution_id=build_execution_id,
                relative_path=a.relative_path,
                artifact_type=a.artifact_type,
                file_size_bytes=a.file_size_bytes,
                strict_sha256=a.strict_sha256,
                normalized_sha256=a.normalized_sha256,
                metadata_json=metadata_dict,
            )
            self.session.add(item)
            records.append(item)

        await self.session.flush()
        return records

    async def get_by_build(self, build_execution_id: str) -> List[ArtifactItemModel]:
        """Retrieve all recorded artifacts for a given build."""
        stmt = select(ArtifactItemModel).where(
            ArtifactItemModel.build_execution_id == build_execution_id
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
