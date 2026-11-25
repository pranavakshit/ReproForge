"""Shared application service for isolated build execution, artifact collection, and persistence."""

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.artifacts.collector import ArtifactCollector
from backend.app.core.config import settings
from backend.app.core.logging import get_logger
from backend.app.database.session import AsyncSessionLocal
from backend.app.execution.runner import (
    BaseBuildBackend,
    BuildExecutionResult,
    DockerBuildBackend,
    LocalSandboxBackend,
    terminate_build,
)
from backend.app.execution.workspace import WorkspaceManager
from backend.app.fingerprinting.engine import fingerprinter
from backend.app.models.entities import BuildExecutionModel
from backend.app.orchestration.detector import BuildConfigurationDetector
from backend.app.orchestration.git import GitRepositoryManager
from backend.app.repositories.artifact_repo import ArtifactRepository
from backend.app.repositories.build_repo import BuildRepository
from backend.app.repositories.fingerprint_repo import FingerprintRepository
from backend.app.repositories.repository_repo import ProjectRepository, RepositoryRepository
from backend.app.schemas.entities import ArtifactItemCreate, BuildStatus

logger = get_logger("reproforge.services.build")


class BuildServiceOutcome(BaseModel):
    """Aggregate result of a build execution service invocation."""

    build_id: str
    status: BuildStatus
    exit_code: int
    duration_seconds: float
    log_file_path: str
    artifacts: List[ArtifactItemCreate] = []
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = {}


class BuildService:
    """Coordinates build execution, sandboxing, artifact collection, and durable persistence."""

    def __init__(
        self,
        session: Optional[AsyncSession] = None,
        workspace_manager: Optional[WorkspaceManager] = None,
        git_manager: Optional[GitRepositoryManager] = None,
        detector: Optional[BuildConfigurationDetector] = None,
        collector: Optional[ArtifactCollector] = None,
    ):
        self._session = session
        self.workspace_manager = workspace_manager or WorkspaceManager()
        self.git_manager = git_manager or GitRepositoryManager()
        self.detector = detector or BuildConfigurationDetector()
        self.collector = collector or ArtifactCollector()

    async def execute_build(
        self,
        repo_path: Path,
        commit_ref: str = "HEAD",
        backend_type: str = "local",
        custom_cmd: Optional[str] = None,
        timeout_seconds: int = 1200,
        build_id: Optional[str] = None,
        project_name: str = "default",
        on_output_chunk: Optional[Callable[[str], None]] = None,
    ) -> BuildServiceOutcome:
        """Execute a full build run and persist execution record and collected artifacts."""
        bid = build_id or f"build-{uuid.uuid4().hex[:10]}"

        if self._session:
            return await self._execute_build_pipeline(
                self._session,
                repo_path,
                commit_ref,
                backend_type,
                custom_cmd,
                timeout_seconds,
                bid,
                project_name,
                on_output_chunk,
            )

        async with AsyncSessionLocal() as session:
            async with session.begin():
                return await self._execute_build_pipeline(
                    session,
                    repo_path,
                    commit_ref,
                    backend_type,
                    custom_cmd,
                    timeout_seconds,
                    bid,
                    project_name,
                    on_output_chunk,
                )

    async def _execute_build_pipeline(
        self,
        session: AsyncSession,
        repo_path: Path,
        commit_ref: str,
        backend_type: str,
        custom_cmd: Optional[str],
        timeout_seconds: int,
        build_id: str,
        project_name: str,
        on_output_chunk: Optional[Callable[[str], None]] = None,
    ) -> BuildServiceOutcome:
        proj_repo = ProjectRepository(session)
        repo_repo = RepositoryRepository(session)
        build_repo = BuildRepository(session)
        art_repo = ArtifactRepository(session)
        fp_repo = FingerprintRepository(session)

        # 1. Resolve Git reference
        resolved_sha = self.git_manager.resolve_commit_sha(repo_path, commit_ref)

        # 2. Persist repository & revision
        project = await proj_repo.get_or_create(project_name)
        repo_model = await repo_repo.get_or_create(
            name=repo_path.name,
            repo_url=str(repo_path.resolve()),
            project_id=project.id,
        )
        commit_meta = self.git_manager.get_commit_metadata(
            repo_path, repository_id=repo_model.id, ref=resolved_sha
        )
        rev_model = await repo_repo.record_revision(
            repository_id=repo_model.id,
            commit_sha=resolved_sha,
            branch_name=commit_ref if commit_ref != "HEAD" else "HEAD",
            author_name=commit_meta.author_name,
            commit_message=commit_meta.commit_message,
        )

        # 3. Capture & persist environment fingerprint
        fp_data = fingerprinter.capture_fingerprint()
        fp_model = await fp_repo.get_or_record(fp_data)

        # 4. Detect build configuration
        systems = self.detector.detect(repo_path)
        primary_cmd = systems[0].suggested_build_command if systems else "make"
        build_cmd = custom_cmd or primary_cmd

        # 5. Allocate workspace & export clean source
        ws_path = self.workspace_manager.allocate_workspace(build_id)
        source_dir = ws_path / "source"

        try:
            self.git_manager.export_clean_revision(repo_path, source_dir, resolved_sha)
        except Exception as e:
            self.workspace_manager.cleanup_workspace(ws_path)
            raise RuntimeError(f"Failed to export source revision {resolved_sha}: {e}") from e

        # 6. Create initial build record (QUEUED)
        build_record = await build_repo.create_build(
            repository_id=repo_model.id,
            revision_id=rev_model.id,
            build_id=build_id,
            profile_name=f"isolated-{backend_type}",
            fingerprint_id=fp_model.id,
        )

        # 7. Transition to BUILDING & Execute runner
        await build_repo.update_status(build_id, BuildStatus.BUILDING)
        runner: BaseBuildBackend = DockerBuildBackend() if backend_type == "docker" else LocalSandboxBackend()

        result: BuildExecutionResult = await runner.execute(
            build_id=build_id,
            workspace_source_dir=source_dir,
            build_command=build_cmd,
            timeout_seconds=timeout_seconds,
            on_output_chunk=on_output_chunk,
        )

        # 8. Update execution status in DB
        final_status = (
            BuildStatus.CANCELLED if result.status == BuildStatus.CANCELLED
            else BuildStatus.COMPLETED if result.exit_code == 0
            else BuildStatus.TIMED_OUT if result.status == BuildStatus.TIMED_OUT
            else BuildStatus.FAILED
        )
        await build_repo.update_status(
            build_id=build_id,
            status=final_status,
            exit_code=result.exit_code,
            duration_seconds=result.duration_seconds,
            log_file_path=result.log_file_path,
        )

        # 9. Collect artifacts if successful
        collected_artifacts: List[ArtifactItemCreate] = []
        if result.exit_code == 0:
            docker_tag = result.metadata.get("image_tag") if backend_type == "docker" else None
            collected_artifacts = self.collector.collect_from_workspace(
                build_id, source_dir, docker_image_tag=docker_tag
            )
            if collected_artifacts:
                await art_repo.record_artifacts(build_id, collected_artifacts)

        return BuildServiceOutcome(
            build_id=build_id,
            status=final_status,
            exit_code=result.exit_code,
            duration_seconds=result.duration_seconds,
            log_file_path=result.log_file_path,
            artifacts=collected_artifacts,
            error_message=result.error_message,
            metadata=result.metadata,
        )

    async def cancel_build(self, build_id: str) -> Optional[BuildExecutionModel]:
        """Terminate an active build process (if running) and mark cancelled in DB."""
        await terminate_build(build_id)
        if self._session:
            return await BuildRepository(self._session).cancel_build(build_id)
        async with AsyncSessionLocal() as session:
            async with session.begin():
                return await BuildRepository(session).cancel_build(build_id)

    async def reconcile_crashed_builds(self) -> List[str]:
        """Durable post-crash startup reconciliation."""
        if self._session:
            return await BuildRepository(self._session).reconcile_interrupted_builds()
        async with AsyncSessionLocal() as session:
            async with session.begin():
                return await BuildRepository(session).reconcile_interrupted_builds()

    async def get_build(self, build_id: str) -> Optional[BuildExecutionModel]:
        """Retrieve build execution record by ID."""
        if self._session:
            return await BuildRepository(self._session).get_by_id(build_id)
        async with AsyncSessionLocal() as session:
            return await BuildRepository(session).get_by_id(build_id)

    async def list_builds(
        self, repository_id: Optional[str] = None, limit: int = 50, offset: int = 0
    ) -> List[BuildExecutionModel]:
        """List historical build executions."""
        if self._session:
            repo = BuildRepository(self._session)
            return (
                await repo.list_by_repository(repository_id, limit, offset)
                if repository_id
                else await repo.list_all(limit, offset)
            )
        async with AsyncSessionLocal() as session:
            repo = BuildRepository(session)
            return (
                await repo.list_by_repository(repository_id, limit, offset)
                if repository_id
                else await repo.list_all(limit, offset)
            )
