"""Repositories for Project and Git Repository persistence."""

from datetime import datetime
from pathlib import Path
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

    @staticmethod
    def canonicalize_repo_url(repo_url: str, repo_type: str = "local") -> str:
        """Normalize local paths to resolved canonical strings and strip trailing slashes on URLs."""
        if repo_type == "local" or ("://" not in repo_url and not repo_url.startswith("git@")):
            candidate = Path(repo_url)
            if candidate.exists():
                return str(candidate.resolve())
        return repo_url.rstrip("/")

    async def get_by_url(self, repo_url: str) -> Optional[RepositoryModel]:
        """Find repository by exact or canonicalized URL/path."""
        canonical = self.canonicalize_repo_url(repo_url)
        stmt = select(RepositoryModel).where(
            RepositoryModel.repo_url.in_([repo_url, canonical])
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def get_or_create(
        self,
        name: str,
        repo_url: str,
        project_id: Optional[str] = None,
        repo_type: str = "local",
        default_branch: str = "main",
        upstream_github_url: Optional[str] = None,
        github_slug: Optional[str] = None,
        scan_metadata_json: Optional[dict] = None,
        last_scanned_at: Optional[datetime] = None,
    ) -> RepositoryModel:
        """Find existing repository or create/update record using canonical path/URL."""
        canonical = self.canonicalize_repo_url(repo_url, repo_type=repo_type)
        existing = await self.get_by_url(canonical)
        if existing:
            if upstream_github_url is not None:
                existing.upstream_github_url = upstream_github_url
            if github_slug is not None:
                existing.github_slug = github_slug
            if scan_metadata_json is not None:
                existing.scan_metadata_json = scan_metadata_json
            if last_scanned_at is not None:
                existing.last_scanned_at = last_scanned_at
            await self.session.flush()
            return existing
        new_repo = RepositoryModel(
            name=name,
            repo_url=canonical,
            project_id=project_id,
            repo_type=repo_type,
            default_branch=default_branch,
            upstream_github_url=upstream_github_url,
            github_slug=github_slug,
            scan_metadata_json=scan_metadata_json or {},
            last_scanned_at=last_scanned_at,
        )
        return await self.add(new_repo)

    async def record_revision(
        self,
        repository_id: str,
        commit_sha: str,
        branch_name: Optional[str] = None,
        author_name: Optional[str] = None,
        commit_message: Optional[str] = None,
        committed_at: Optional[datetime] = None,
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
            committed_at=committed_at,
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
