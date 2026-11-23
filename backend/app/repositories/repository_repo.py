"""Repositories for Project and Git Repository persistence."""

from typing import List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.models.entities import ProjectModel, RepositoryModel, SourceRevisionModel
from backend.app.repositories.base import BaseRepository


class ProjectRepository(BaseRepository[ProjectModel]):
    """Async repository for Project entities."""

    def __init__(self, session: AsyncSession):
        super().__init__(ProjectModel, session)

    async def get_by_name(self, name: str) -> Optional[ProjectModel]:
        """Find project by unique name."""
        stmt = select(ProjectModel).where(ProjectModel.name == name)
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def get_or_create(self, name: str, description: Optional[str] = None) -> ProjectModel:
        """Retrieve existing project or create a new one."""
        proj = await self.get_by_name(name)
        if proj:
            return proj
        new_proj = ProjectModel(name=name, description=description)
        return await self.add(new_proj)


class RepositoryRepository(BaseRepository[RepositoryModel]):
    """Async repository for Git Repository entities."""

    def __init__(self, session: AsyncSession):
        super().__init__(RepositoryModel, session)

    async def get_by_url(self, repo_url: str) -> Optional[RepositoryModel]:
        """Find repository by URL/path."""
        stmt = select(RepositoryModel).where(RepositoryModel.repo_url == repo_url)
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def get_or_create(
        self,
        name: str,
        repo_url: str,
        project_id: Optional[str] = None,
        repo_type: str = "local",
        default_branch: str = "main",
    ) -> RepositoryModel:
        """Find existing repository or create a new record."""
        existing = await self.get_by_url(repo_url)
        if existing:
            return existing
        new_repo = RepositoryModel(
            name=name,
            repo_url=repo_url,
            project_id=project_id,
            repo_type=repo_type,
            default_branch=default_branch,
        )
        return await self.add(new_repo)

    async def record_revision(
        self,
        repository_id: str,
        commit_sha: str,
        branch_name: Optional[str] = None,
        author_name: Optional[str] = None,
        commit_message: Optional[str] = None,
    ) -> SourceRevisionModel:
        """Record or retrieve a specific Git source revision for a repository."""
        stmt = select(SourceRevisionModel).where(
            SourceRevisionModel.repository_id == repository_id,
            SourceRevisionModel.commit_sha == commit_sha,
        )
        result = await self.session.execute(stmt)
        rev = result.scalars().first()
        if rev:
            return rev

        new_rev = SourceRevisionModel(
            repository_id=repository_id,
            commit_sha=commit_sha,
            branch_name=branch_name,
            author_name=author_name,
            commit_message=commit_message,
        )
        self.session.add(new_rev)
        await self.session.flush()
        return new_rev

    async def list_revisions(self, repository_id: str, limit: int = 50) -> List[SourceRevisionModel]:
        """List historical revisions recorded for a repository."""
        stmt = (
            select(SourceRevisionModel)
            .where(SourceRevisionModel.repository_id == repository_id)
            .order_by(SourceRevisionModel.created_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
