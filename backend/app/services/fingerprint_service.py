"""Shared application service for Environment Fingerprinting and telemetry."""

from typing import Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.database.session import AsyncSessionLocal
from backend.app.fingerprinting.engine import EnvironmentFingerprintEngine, fingerprinter
from backend.app.models.entities import EnvironmentFingerprintModel
from backend.app.repositories.fingerprint_repo import FingerprintRepository
from backend.app.schemas.entities import (
    EnvironmentFingerprintCreate,
    SystemInfo,
    ToolchainInfo,
)


class FingerprintService:
    """Coordinates environment capture, secret redaction, and database persistence."""

    def __init__(
        self,
        session: Optional[AsyncSession] = None,
        engine: Optional[EnvironmentFingerprintEngine] = None,
    ):
        self._session = session
        self.engine = engine or fingerprinter
        self._cached_telemetry: Optional[Tuple[SystemInfo, ToolchainInfo]] = None

    def get_telemetry(self, use_cache: bool = False) -> Tuple[SystemInfo, ToolchainInfo]:
        """Capture host system telemetry and detected build toolchains (optionally cached)."""
        if use_cache and self._cached_telemetry is not None:
            return self._cached_telemetry
        sys_info = self.engine.capture_system_info()
        tools = self.engine.capture_toolchain_info()
        self._cached_telemetry = (sys_info, tools)
        return sys_info, tools

    def capture_fingerprint(self) -> EnvironmentFingerprintCreate:
        """Capture full canonical environment fingerprint without persistence."""
        return self.engine.capture_fingerprint()

    async def capture_and_persist(self, repo_id: Optional[str] = None) -> EnvironmentFingerprintModel:
        """Capture current host environment and persist canonical record in SQLite."""
        _ = repo_id  # Reserved for repository-scoped telemetry tags
        fp = self.capture_fingerprint()

        if self._session:
            repo = FingerprintRepository(self._session)
            return await repo.get_or_record(fp)

        async with AsyncSessionLocal() as session:
            async with session.begin():
                repo = FingerprintRepository(session)
                return await repo.get_or_record(fp)

    async def get_by_hash(self, fingerprint_hash: str) -> Optional[EnvironmentFingerprintModel]:
        """Retrieve fingerprint by its SHA-256 cryptographic digest."""
        if self._session:
            repo = FingerprintRepository(self._session)
            return await repo.get_by_hash(fingerprint_hash)

        async with AsyncSessionLocal() as session:
            repo = FingerprintRepository(session)
            return await repo.get_by_hash(fingerprint_hash)
