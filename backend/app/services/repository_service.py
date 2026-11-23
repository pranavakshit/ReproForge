"""Shared application service for Project, Git Repository, and Revision management."""

from pathlib import Path
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.database.session import AsyncSessionLocal
from backend.app.models.entities import RepositoryModel, SourceRevisionModel
from backend.app.orchestration.git import GitRepositoryManager
from backend.app.repositories.repository_repo import ProjectRepository, RepositoryRepository


class RepositoryService:
    """Coordinates repository registration and Git revision inspection with persistence."""

    def __init__(
        self,
        session: Optional[AsyncSession] = None,
        git_manager: Optional[GitRepositoryManager] = None,
    ):
        self._session = session
        self.git_manager = git_manager or GitRepositoryManager()

    async def register_repository(
        self,
        name: str,
        repo_url: str,
        project_name: str = "default",
        repo_type: str = "local",
        default_branch: str = "main",
    ) -> RepositoryModel:
        """Register or retrieve a project and repository record."""
        if self._session:
            return await self._register_repository(
                self._session, name, repo_url, project_name, repo_type, default_branch
            )
        async with AsyncSessionLocal() as session:
            async with session.begin():
                return await self._register_repository(
                    session, name, repo_url, project_name, repo_type, default_branch
                )

    async def _register_repository(
        self,
        session: AsyncSession,
        name: str,
        repo_url: str,
        project_name: str,
        repo_type: str,
        default_branch: str,
    ) -> RepositoryModel:
        proj_repo = ProjectRepository(session)
        repo_repo = RepositoryRepository(session)

        project = await proj_repo.get_or_create(project_name)
        return await repo_repo.get_or_create(
            name=name,
            repo_url=str(repo_url),
            project_id=project.id,
            repo_type=repo_type,
            default_branch=default_branch,
        )

    async def resolve_and_record_revision(
        self,
        repository_id: str,
        repo_path: Path,
        commit_ref: str = "HEAD",
    ) -> SourceRevisionModel:
        """Resolve a Git reference to a pinned SHA, extract metadata, and persist revision."""
        resolved_sha = self.git_manager.resolve_commit_sha(repo_path, commit_ref)
        meta = self.git_manager.get_commit_metadata(repo_path, repository_id=repository_id, ref=resolved_sha)

        if self._session:
            repo_repo = RepositoryRepository(self._session)
            return await repo_repo.record_revision(
                repository_id=repository_id,
                commit_sha=resolved_sha,
                branch_name=commit_ref if commit_ref != "HEAD" else "HEAD",
                author_name=meta.author_name,
                commit_message=meta.commit_message,
            )

        async with AsyncSessionLocal() as session:
            async with session.begin():
                repo_repo = RepositoryRepository(session)
                return await repo_repo.record_revision(
                    repository_id=repository_id,
                    commit_sha=resolved_sha,
                    branch_name=commit_ref if commit_ref != "HEAD" else "HEAD",
                    author_name=meta.author_name,
                    commit_message=meta.commit_message,
                )

    async def list_repositories(self, limit: int = 50, offset: int = 0) -> list[RepositoryModel]:
        """List registered repositories."""
        if self._session:
            return await RepositoryRepository(self._session).list_all(limit, offset)
        async with AsyncSessionLocal() as session:
            return await RepositoryRepository(session).list_all(limit, offset)

    async def get_repository(self, repository_id: str) -> Optional[RepositoryModel]:
        """Retrieve repository by ID."""
        if self._session:
            return await RepositoryRepository(self._session).get_by_id(repository_id)
        async with AsyncSessionLocal() as session:
            return await RepositoryRepository(session).get_by_id(repository_id)

    async def get_revisions(self, repository_id: str, limit: int = 50) -> list[SourceRevisionModel]:
        """Retrieve recorded revisions, syncing with local Git log if repository is local on disk."""
        if self._session:
            return await self._get_revisions(self._session, repository_id, limit)
        async with AsyncSessionLocal() as session:
            async with session.begin():
                return await self._get_revisions(session, repository_id, limit)

    async def _get_revisions(
        self, session: AsyncSession, repository_id: str, limit: int
    ) -> list[SourceRevisionModel]:
        repo_repo = RepositoryRepository(session)
        revs = await repo_repo.list_revisions(repository_id, limit)
        if revs:
            return revs

        # If no revisions recorded yet, check if repo_url is a readable Git path on disk
        repo = await repo_repo.get_by_id(repository_id)
        if not repo:
            return []

        local_path = Path(repo.repo_url)
        if local_path.exists() and (local_path / ".git").exists():
            recent = self.git_manager.get_recent_commits(local_path, repository_id=repository_id, limit=limit)
            recorded: list[SourceRevisionModel] = []
            for r in recent:
                saved = await repo_repo.record_revision(
                    repository_id=repository_id,
                    commit_sha=r.commit_sha,
                    branch_name=r.branch_name,
                    author_name=r.author_name,
                    commit_message=r.commit_message,
                )
                recorded.append(saved)
            return recorded

        return []

