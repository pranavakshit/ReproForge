"""Seeds and synchronizes real local repositories and their Git commit histories into ReproForge database."""

import asyncio
import logging
from pathlib import Path
from typing import Dict, List, Any

from backend.app.database.session import AsyncSessionLocal
from backend.app.orchestration.git import GitRepositoryManager
from backend.app.orchestration.github_scanner import GitHubAutoScanner
from backend.app.repositories.repository_repo import ProjectRepository, RepositoryRepository

logger = logging.getLogger(__name__)

REAL_REPOSITORIES = [
    {
        "name": "ReproForge",
        "repo_url": "D:/Repositories/ReproForge",
        "project_name": "ReproForge Capstone",
        "default_branch": "main",
        "repo_type": "local",
    },
    {
        "name": "LocAi",
        "repo_url": "D:/Repositories/LocAi",
        "project_name": "AI Ecosystem",
        "default_branch": "main",
        "repo_type": "local",
    },
    {
        "name": "offline-doc-assistant",
        "repo_url": "D:/Repositories/offline-doc-assistant",
        "project_name": "IOCL AI Suite",
        "default_branch": "main",
        "repo_type": "local",
    },
    {
        "name": "4-Zone-Keyboard-RGB-Toolkit",
        "repo_url": "D:/Repositories/4-Zone-Keyboard-RGB-Toolkit",
        "project_name": "Desktop Utilities",
        "default_branch": "feature/start-minimized",
        "repo_type": "local",
    },
    {
        "name": "ssheasy",
        "repo_url": "D:/Repositories/ssheasy",
        "project_name": "Infrastructure & Networking",
        "default_branch": "master",
        "repo_type": "local",
    },
]


async def seed_real_repositories(limit_commits_per_repo: int = 30) -> List[Dict[str, Any]]:
    """Register all defined real local repositories, ingest actual Git commits, and run GitHub autoscan."""
    git_mgr = GitRepositoryManager()
    scanner = GitHubAutoScanner(git_manager=git_mgr)
    results = []

    async with AsyncSessionLocal() as session:
        async with session.begin():
            proj_repo = ProjectRepository(session)
            repo_repo = RepositoryRepository(session)

            for spec in REAL_REPOSITORIES:
                repo_path = Path(spec["repo_url"])
                if not repo_path.exists():
                    logger.warning("Repository path does not exist: %s", repo_path)
                    continue

                scan_meta = await scanner.scan_local_repository(repo_path, fetch_online_metadata=False)
                from datetime import datetime, timezone
                proj = await proj_repo.get_or_create(spec["project_name"])
                repo_model = await repo_repo.get_or_create(
                    name=spec["name"],
                    repo_url=str(repo_path.resolve()),
                    project_id=proj.id,
                    repo_type=spec["repo_type"],
                    default_branch=scan_meta.get("active_branch") or spec["default_branch"],
                    upstream_github_url=scan_meta.get("upstream_github_url"),
                    github_slug=scan_meta.get("github_slug"),
                    scan_metadata_json=scan_meta,
                    last_scanned_at=datetime.now(timezone.utc).replace(tzinfo=None),
                )

                # Ingest actual Git commits from the repository
                commits = git_mgr.get_recent_commits(
                    repo_path=repo_path,
                    repository_id=repo_model.id,
                    limit=limit_commits_per_repo,
                )

                recorded_count = 0
                for c in commits:
                    # Strip timezone for SQLite DateTime compatibility if present
                    committed_at_naive = c.committed_at.replace(tzinfo=None) if c.committed_at else None
                    await repo_repo.record_revision(
                        repository_id=repo_model.id,
                        commit_sha=c.commit_sha,
                        branch_name=c.branch_name,
                        author_name=c.author_name,
                        commit_message=c.commit_message,
                        committed_at=committed_at_naive,
                    )
                    recorded_count += 1

                results.append({
                    "id": repo_model.id,
                    "name": repo_model.name,
                    "repo_url": repo_model.repo_url,
                    "default_branch": repo_model.default_branch,
                    "commits_ingested": recorded_count,
                })

    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    res = asyncio.run(seed_real_repositories())
    for r in res:
        print(f"Registered {r['name']} ({r['id']}): {r['commits_ingested']} commits ingested.")
