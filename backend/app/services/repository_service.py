"""Shared application service for Project, Git Repository, GitHub Autoscan, and Revision management."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.database.session import AsyncSessionLocal
from backend.app.models.entities import RepositoryModel, SourceRevisionModel
from backend.app.orchestration.git import GitRepositoryManager
from backend.app.orchestration.github_scanner import GitHubAutoScanner
from backend.app.repositories.repository_repo import ProjectRepository, RepositoryRepository


class RepositoryService:
    """Coordinates repository registration, GitHub autoscan, and Git revision inspection."""

    def __init__(
        self,
        session: Optional[AsyncSession] = None,
        git_manager: Optional[GitRepositoryManager] = None,
        github_scanner: Optional[GitHubAutoScanner] = None,
    ):
        self._session = session
        self.git_manager = git_manager or GitRepositoryManager()
        self.github_scanner = github_scanner or GitHubAutoScanner(git_manager=self.git_manager)

    async def register_repository(
        self,
        name: str,
        repo_url: str,
        project_name: str = "default",
        repo_type: str = "local",
        default_branch: str = "main",
        autoscan: bool = True,
        fetch_online_metadata: bool = False,
    ) -> RepositoryModel:
        """Register or retrieve a project & repository, automatically scanning linked GitHub & commits."""
        if self._session:
            return await self._register_repository(
                self._session, name, repo_url, project_name, repo_type, default_branch, autoscan, fetch_online_metadata
            )
        async with AsyncSessionLocal() as session:
            async with session.begin():
                return await self._register_repository(
                    session, name, repo_url, project_name, repo_type, default_branch, autoscan, fetch_online_metadata
                )

    async def _register_repository(
        self,
        session: AsyncSession,
        name: str,
        repo_url: str,
        project_name: str,
        repo_type: str,
        default_branch: str,
        autoscan: bool = True,
        fetch_online_metadata: bool = False,
    ) -> RepositoryModel:
        proj_repo = ProjectRepository(session)
        repo_repo = RepositoryRepository(session)

        local_path = Path(repo_url)
        resolved_url = repo_url
        upstream_url: Optional[str] = None
        github_slug: Optional[str] = None
        scan_meta: Dict[str, Any] = {}
        scanned_at: Optional[datetime] = None
        branch = default_branch

        # If a direct GitHub URL was passed, support parsing / clean local checkout when requested
        gh_parsed = self.git_manager.parse_github_remote_url(repo_url)
        if gh_parsed and not local_path.exists():
            github_slug, upstream_url = gh_parsed
            repo_type = "github"
        elif local_path.exists() and (local_path / ".git").exists():
            resolved_url = str(local_path.resolve())
            repo_type = "local"
            if autoscan:
                scan_meta = await self.github_scanner.scan_local_repository(
                    local_path, fetch_online_metadata=fetch_online_metadata
                )
                upstream_url = scan_meta.get("upstream_github_url")
                github_slug = scan_meta.get("github_slug")
                branch = scan_meta.get("active_branch") or default_branch
                scanned_at = datetime.now(timezone.utc).replace(tzinfo=None)

        project = await proj_repo.get_or_create(project_name)
        repo = await repo_repo.get_or_create(
            name=name,
            repo_url=resolved_url,
            project_id=project.id,
            repo_type=repo_type,
            default_branch=branch,
            upstream_github_url=upstream_url,
            github_slug=github_slug,
            scan_metadata_json=scan_meta,
            last_scanned_at=scanned_at,
        )

        # Immediately ingest real Git revisions on registration if local repo exists
        if local_path.exists() and (local_path / ".git").exists():
            recent = self.git_manager.get_recent_commits(local_path, repository_id=repo.id, limit=30)
            for c in recent:
                committed_naive = c.committed_at.replace(tzinfo=None) if c.committed_at else None
                await repo_repo.record_revision(
                    repository_id=repo.id,
                    commit_sha=c.commit_sha,
                    branch_name=c.branch_name,
                    author_name=c.author_name,
                    commit_message=c.commit_message,
                    committed_at=committed_naive,
                )

        return repo

    async def discover_folder_candidates(self, folder_path: Path) -> Dict[str, Any]:
        """Discover single or 1-level child Git repositories for checklist selection."""
        existing_repos = await self.list_repositories(limit=500)
        tracked_urls = {str(Path(r.repo_url).resolve()) for r in existing_repos if Path(r.repo_url).exists()}
        return self.github_scanner.discover_folder_candidates(folder_path, tracked_urls=tracked_urls)

    async def register_batch(
        self,
        items: List[Dict[str, str]],
        project_name: str = "Workspace Imports",
        fetch_online_metadata: bool = True,
    ) -> List[RepositoryModel]:
        """Register and auto-scan multiple selected repositories from a parent workspace checklist."""
        created: List[RepositoryModel] = []
        for item in items:
            p = Path(item["repo_path"])
            if not p.exists():
                continue
            repo = await self.register_repository(
                name=item.get("name") or p.name,
                repo_url=str(p.resolve()),
                project_name=project_name,
                repo_type="local",
                default_branch=item.get("default_branch", "main"),
                autoscan=True,
                fetch_online_metadata=fetch_online_metadata,
            )
            created.append(repo)
        return created

    async def autoscan_repository(
        self, repository_id: str, fetch_online_metadata: bool = True
    ) -> Optional[RepositoryModel]:
        """Re-run GitHub upstream and static reproducibility autoscan on a tracked repository."""
        repo = await self.get_repository(repository_id)
        if not repo:
            return None
        local_path = Path(repo.repo_url)
        if not (local_path.exists() and (local_path / ".git").exists()):
            return repo
        return await self.register_repository(
            name=repo.name,
            repo_url=repo.repo_url,
            repo_type=repo.repo_type,
            default_branch=repo.default_branch,
            autoscan=True,
            fetch_online_metadata=fetch_online_metadata,
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

