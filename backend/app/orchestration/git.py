"""Git repository ingestion and exact commit revision pinning."""

import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from backend.app.schemas.entities import SourceRevisionCreate
from backend.app.core.logging import get_logger

logger = get_logger("reproforge.orchestration.git")


class GitRepositoryError(Exception):
    """Raised when git repository operations fail."""
    pass


class GitRepositoryManager:
    """Manages git repository ingestion, commit resolution, and clean worktree extraction."""

    def __init__(self, git_binary: str = "git") -> None:
        self.git_binary = git_binary
        self._ensure_git_available()

    def _ensure_git_available(self) -> None:
        """Verify git executable exists on system PATH."""
        if not shutil.which(self.git_binary):
            raise GitRepositoryError(
                f"Git binary '{self.git_binary}' not found on system PATH."
            )

    def _run_git_cmd(self, cmd: list[str], cwd: Optional[Path] = None) -> str:
        """Run a git command safely and return standard output."""
        full_cmd = [self.git_binary] + cmd
        try:
            result = subprocess.run(
                full_cmd,
                cwd=str(cwd) if cwd else None,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                error_msg = result.stderr.strip() or result.stdout.strip()
                raise GitRepositoryError(
                    f"Git command {' '.join(full_cmd)} failed (code {result.returncode}): {error_msg}"
                )
            return result.stdout.strip()
        except FileNotFoundError:
            raise GitRepositoryError(f"Git executable not found: {self.git_binary}")
        except Exception as e:
            if not isinstance(e, GitRepositoryError):
                raise GitRepositoryError(f"Unexpected error running git: {e}") from e
            raise

    def resolve_commit_sha(self, repo_path: Path, ref: str = "HEAD") -> str:
        """Strictly resolve a branch name, tag, or partial ref to a full 40-character commit SHA."""
        if not repo_path.exists():
            raise GitRepositoryError(f"Repository path does not exist: {repo_path}")

        sha = self._run_git_cmd(["rev-parse", ref], cwd=repo_path)
        if len(sha) != 40:
            raise GitRepositoryError(f"Resolved SHA '{sha}' is not a valid 40-character commit hash.")
        return sha

    def get_commit_metadata(
        self, repo_path: Path, repository_id: str, ref: str = "HEAD"
    ) -> SourceRevisionCreate:
        """Extract author, timestamp, commit message, and full SHA for the given reference."""
        # %H: commit hash, %an: author name, %ct: committer date UNIX timestamp, %s: subject
        format_spec = "%H%x1f%an%x1f%ct%x1f%s"
        output = self._run_git_cmd(["log", "-1", f"--format={format_spec}", ref], cwd=repo_path)
        parts = output.split("\x1f")
        if len(parts) < 4:
            raise GitRepositoryError(f"Could not parse git log output for ref {ref}: {output}")

        commit_sha = parts[0].strip()
        author_name = parts[1].strip()
        timestamp_epoch = int(parts[2].strip())
        commit_message = parts[3].strip()

        committed_at = datetime.fromtimestamp(timestamp_epoch, tz=timezone.utc)

        return SourceRevisionCreate(
            repository_id=repository_id,
            commit_sha=commit_sha,
            branch_name=ref if ref != "HEAD" else None,
            author_name=author_name,
            commit_message=commit_message,
            committed_at=committed_at,
        )

    def get_recent_commits(
        self, repo_path: Path, repository_id: str, limit: int = 20
    ) -> list[SourceRevisionCreate]:
        """Extract recent commit history for the repository."""
        format_spec = "%H%x1f%an%x1f%ct%x1f%s"
        try:
            output = self._run_git_cmd(["log", f"-{limit}", f"--format={format_spec}"], cwd=repo_path)
        except Exception:
            return []

        revisions: list[SourceRevisionCreate] = []
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("\x1f")
            if len(parts) < 4:
                continue
            commit_sha = parts[0].strip()
            author_name = parts[1].strip()
            try:
                timestamp_epoch = int(parts[2].strip())
                committed_at = datetime.fromtimestamp(timestamp_epoch, tz=timezone.utc)
            except Exception:
                committed_at = datetime.now(timezone.utc)
            commit_message = parts[3].strip()

            revisions.append(
                SourceRevisionCreate(
                    repository_id=repository_id,
                    commit_sha=commit_sha,
                    branch_name="HEAD",
                    author_name=author_name,
                    commit_message=commit_message,
                    committed_at=committed_at,
                )
            )
        return revisions

    def export_clean_revision(self, repo_path: Path, target_dir: Path, commit_sha: str) -> Path:
        """Export a clean, isolated source tree for the given commit without untracked files or .git history."""
        target_dir.mkdir(parents=True, exist_ok=True)
        # Using git archive piped to tar extraction for a 100% clean source tree
        archive_tar = target_dir / "_source_archive.tar"
        try:
            # Create tar archive from git
            self._run_git_cmd(["archive", "--format=tar", f"--output={archive_tar}", commit_sha], cwd=repo_path)
            
            # Unpack tar safely using Python tarfile data filter
            import tarfile
            with tarfile.open(archive_tar, "r") as tar:
                tar.extractall(path=target_dir, filter="data")
            
            # Remove temporary tar
            if archive_tar.exists():
                archive_tar.unlink()
            
            logger.info(f"Clean source revision {commit_sha[:8]} exported to {target_dir}")
            return target_dir
        except Exception as e:
            if archive_tar.exists():
                archive_tar.unlink()
            raise GitRepositoryError(f"Failed to export clean revision {commit_sha}: {e}") from e

    @staticmethod
    def parse_github_remote_url(raw_url: str) -> Optional[Tuple[str, str]]:
        """Normalize SSH/HTTPS GitHub remote URLs into (owner/repo, https_url) or None."""
        cleaned = (raw_url or "").strip()
        if not cleaned:
            return None

        patterns = [
            r"^git@github\.com:(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$",
            r"^ssh://git@github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$",
            r"^https?://(?:[^@/]+@)?github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$",
        ]
        for pat in patterns:
            m = re.match(pat, cleaned, flags=re.IGNORECASE)
            if m:
                slug = f"{m.group('owner')}/{m.group('repo')}"
                return slug, f"https://github.com/{slug}"
        return None

    def get_remotes(self, repo_path: Path) -> Dict[str, str]:
        """Return configured git remote fetch URLs keyed by remote name."""
        try:
            output = self._run_git_cmd(["remote", "-v"], cwd=repo_path)
        except GitRepositoryError:
            return {}

        remotes: Dict[str, str] = {}
        for line in output.splitlines():
            parts = line.split()
            if len(parts) >= 2 and ("(fetch)" in line or parts[0] not in remotes):
                remotes[parts[0]] = parts[1]
        return remotes

    def get_active_branch(self, repo_path: Path) -> str:
        """Return current branch name or fallback 'main'."""
        try:
            branch = self._run_git_cmd(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_path)
            if branch and branch != "HEAD":
                return branch
        except GitRepositoryError:
            pass
        return "main"

    def inspect_remote_alignment(
        self, repo_path: Path, remote_name: str = "origin", timeout: int = 4
    ) -> Dict[str, Any]:
        """Non-destructively probe remote HEAD via `git ls-remote` without modifying local tree."""
        remotes = self.get_remotes(repo_path)
        if remote_name not in remotes:
            remote_name = next(iter(remotes), "")
        if not remote_name:
            return {"reachable": False, "status": "no_remote", "remote_head_sha": None}

        try:
            local_head = self.resolve_commit_sha(repo_path, "HEAD")
        except GitRepositoryError:
            local_head = None

        try:
            res = subprocess.run(
                [self.git_binary, "ls-remote", remotes[remote_name], "HEAD", "refs/heads/*"],
                cwd=str(repo_path),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            if res.returncode != 0:
                return {
                    "reachable": False,
                    "status": "offline_or_auth_required",
                    "local_head_sha": local_head,
                    "remote_head_sha": None,
                }

            remote_head_sha = None
            remote_branches: List[str] = []
            for line in res.stdout.splitlines():
                parts = line.split()
                if len(parts) < 2:
                    continue
                sha, ref = parts[0], parts[1]
                if ref == "HEAD":
                    remote_head_sha = sha
                elif ref.startswith("refs/heads/"):
                    remote_branches.append(ref.replace("refs/heads/", "", 1))

            aligned = bool(local_head and remote_head_sha and local_head == remote_head_sha)
            return {
                "reachable": True,
                "status": "in_sync" if aligned else "diverged_from_upstream",
                "remote_name": remote_name,
                "local_head_sha": local_head,
                "remote_head_sha": remote_head_sha,
                "remote_branches": remote_branches[:20],
            }
        except Exception as exc:
            return {
                "reachable": False,
                "status": f"probe_skipped: {exc}",
                "local_head_sha": local_head,
                "remote_head_sha": None,
            }

    def shallow_clone_github(self, github_url: str, dest_dir: Path) -> Path:
        """Clone a remote GitHub repository safely with shallow depth and no script execution."""
        parsed = self.parse_github_remote_url(github_url)
        if not parsed:
            raise GitRepositoryError(f"Unsupported GitHub URL format: {github_url}")
        _, canonical_url = parsed
        if (dest_dir / ".git").exists():
            return dest_dir

        dest_dir.parent.mkdir(parents=True, exist_ok=True)
        res = subprocess.run(
            [self.git_binary, "clone", "--depth", "50", canonical_url, str(dest_dir)],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if res.returncode != 0:
            raise GitRepositoryError(f"Git clone failed: {res.stderr.strip() or res.stdout.strip()}")
        return dest_dir

    def discover_git_repos_in_folder(self, folder_path: Path) -> List[Dict[str, Any]]:
        """Discover a repository at folder_path or 1-level child Git repositories for checklist selection."""
        resolved = folder_path.resolve()
        if not resolved.exists() or not resolved.is_dir():
            return []

        candidates: List[Path] = []
        if (resolved / ".git").exists():
            candidates = [resolved]
        else:
            for child in sorted(resolved.iterdir(), key=lambda p: p.name.lower()):
                if child.is_dir() and not child.name.startswith(".") and (child / ".git").exists():
                    candidates.append(child)

        discovered: List[Dict[str, Any]] = []
        for repo_dir in candidates:
            remotes = self.get_remotes(repo_dir)
            gh_info = None
            for _, r_url in remotes.items():
                gh_info = self.parse_github_remote_url(r_url)
                if gh_info:
                    break
            discovered.append({
                "name": repo_dir.name,
                "repo_path": str(repo_dir),
                "default_branch": self.get_active_branch(repo_dir),
                "github_slug": gh_info[0] if gh_info else "",
                "upstream_github_url": gh_info[1] if gh_info else "",
                "is_git_repo": True,
            })
        return discovered
