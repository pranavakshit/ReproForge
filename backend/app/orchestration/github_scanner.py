"""Non-destructive GitHub remote detection and reproducibility autoscan service."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from backend.app.core.config import settings
from backend.app.core.logging import get_logger
from backend.app.orchestration.detector import BuildConfigurationDetector
from backend.app.orchestration.git import GitRepositoryManager

logger = get_logger("reproforge.orchestration.github_scanner")


class GitHubAutoScanner:
    """Inspects local repositories for linked GitHub remotes and audits CI/build readiness."""

    def __init__(
        self,
        git_manager: Optional[GitRepositoryManager] = None,
        detector: Optional[BuildConfigurationDetector] = None,
    ) -> None:
        self.git_manager = git_manager or GitRepositoryManager()
        self.detector = detector or BuildConfigurationDetector()

    async def _fetch_public_github_metadata(self, slug: str, timeout: float = 3.0) -> Dict[str, Any]:
        """Perform non-destructive read-only lookup of public GitHub repository metadata."""
        api_url = f"https://api.github.com/repos/{slug}"
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(
                    api_url,
                    headers={"Accept": "application/vnd.github+json", "User-Agent": "ReproForge/0.1.0"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return {
                        "api_reachable": True,
                        "default_branch": data.get("default_branch"),
                        "visibility": data.get("visibility", "public" if not data.get("private") else "private"),
                        "stars": data.get("stargazers_count", 0),
                        "pushed_at": data.get("pushed_at"),
                        "description": data.get("description") or "",
                    }
                return {"api_reachable": False, "status_code": resp.status_code}
        except Exception as exc:
            logger.debug(f"Offline/skipped GitHub metadata fetch for {slug}: {exc}")
            return {"api_reachable": False, "reason": "offline_or_unreachable"}

    async def scan_local_repository(
        self, repo_path: Path, fetch_online_metadata: bool = True
    ) -> Dict[str, Any]:
        """Inspect a local Git folder: detect GitHub upstream, alignment, workflows, & lockfiles."""
        resolved = repo_path.resolve()
        remotes = self.git_manager.get_remotes(resolved)
        active_branch = self.git_manager.get_active_branch(resolved)

        github_slug: Optional[str] = None
        upstream_url: Optional[str] = None
        primary_remote_name = "origin"

        # Prefer 'origin', otherwise first GitHub remote found
        ordered_remotes = []
        if "origin" in remotes:
            ordered_remotes.append(("origin", remotes["origin"]))
        for r_name, r_url in remotes.items():
            if r_name != "origin":
                ordered_remotes.append((r_name, r_url))

        for r_name, r_url in ordered_remotes:
            parsed = self.git_manager.parse_github_remote_url(r_url)
            if parsed:
                github_slug, upstream_url = parsed
                primary_remote_name = r_name
                break

        alignment: Dict[str, Any] = {"reachable": False, "status": "no_github_remote"}
        github_api_meta: Dict[str, Any] = {}

        if github_slug and upstream_url:
            alignment = self.git_manager.inspect_remote_alignment(resolved, remote_name=primary_remote_name)
            if fetch_online_metadata:
                github_api_meta = await self._fetch_public_github_metadata(github_slug)

        readiness = self.detector.audit_reproducibility_readiness(resolved)

        return {
            "scanned_at": datetime.now(timezone.utc).isoformat(),
            "repo_path": str(resolved),
            "active_branch": active_branch,
            "remotes": remotes,
            "github_slug": github_slug,
            "upstream_github_url": upstream_url,
            "upstream_alignment": alignment,
            "github_metadata": github_api_meta,
            "readiness": readiness,
        }

    def discover_folder_candidates(
        self, folder_path: Path, tracked_urls: Optional[set[str]] = None
    ) -> Dict[str, Any]:
        """Inspect a local folder: returns single-repo info or checklist of 1-level child Git repos."""
        resolved = folder_path.resolve()
        if not resolved.exists() or not resolved.is_dir():
            return {"mode": "invalid", "folder": str(folder_path), "candidates": []}

        is_single = (resolved / ".git").exists()
        candidates = self.git_manager.discover_git_repos_in_folder(resolved)
        tracked_set = tracked_urls or set()

        for c in candidates:
            c["already_tracked"] = c["repo_path"] in tracked_set

        return {
            "mode": "single_repo" if is_single else "workspace_checklist",
            "folder": str(resolved),
            "candidates": candidates,
        }

    def prepare_github_url_clone(self, github_url: str) -> Path:
        """Clone or reuse a clean shallow copy of a GitHub URL inside data/cloned_repos."""
        parsed = self.git_manager.parse_github_remote_url(github_url)
        if not parsed:
            raise ValueError(f"Invalid GitHub URL: {github_url}")
        slug, _ = parsed
        owner, repo = slug.split("/", 1)
        dest = settings.DATA_DIR / "cloned_repos" / owner / repo
        return self.git_manager.shallow_clone_github(github_url, dest)
