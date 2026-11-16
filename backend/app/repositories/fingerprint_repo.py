"""Repository for EnvironmentFingerprint entity persistence."""

from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.models.entities import EnvironmentFingerprintModel
from backend.app.repositories.base import BaseRepository
from backend.app.schemas.entities import EnvironmentFingerprintBase


class FingerprintRepository(BaseRepository[EnvironmentFingerprintModel]):
    """Async repository for canonical environment fingerprints."""

    def __init__(self, session: AsyncSession):
        super().__init__(EnvironmentFingerprintModel, session)

    async def get_by_hash(self, fingerprint_hash: str) -> Optional[EnvironmentFingerprintModel]:
        """Find an environment fingerprint by its cryptographic digest."""
        stmt = select(EnvironmentFingerprintModel).where(
            EnvironmentFingerprintModel.fingerprint_hash == fingerprint_hash
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def get_or_record(self, fp: EnvironmentFingerprintBase) -> EnvironmentFingerprintModel:
        """Find existing fingerprint by hash or persist a new record."""
        existing = await self.get_by_hash(fp.fingerprint_hash)
        if existing:
            return existing

        model = EnvironmentFingerprintModel(
            fingerprint_hash=fp.fingerprint_hash,
            system_info=fp.system_info.model_dump(mode="json"),
            toolchain_info=fp.toolchain_info.model_dump(mode="json"),
            locale_info=fp.locale_info.model_dump(mode="json"),
            environment_variables=fp.environment_variables,
            container_runtime_info=fp.container_runtime_info,
        )
        return await self.add(model)
