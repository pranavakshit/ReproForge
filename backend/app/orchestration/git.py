"""Git repository ingestion and exact commit revision pinning."""

import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
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

    def export_clean_revision(self, repo_path: Path, target_dir: Path, commit_sha: str) -> Path:
        """Export a clean, isolated source tree for the given commit without untracked files or .git history."""
        target_dir.mkdir(parents=True, exist_ok=True)
        # Using git archive piped to tar extraction for a 100% clean source tree
        archive_tar = target_dir / "_source_archive.tar"
        try:
            # Create tar archive from git
            self._run_git_cmd(["archive", "--format=tar", f"--output={archive_tar}", commit_sha], cwd=repo_path)
            
            # Unpack tar safely using Python tarfile
            import tarfile
            with tarfile.open(archive_tar, "r") as tar:
                tar.extractall(path=target_dir)
            
            # Remove temporary tar
            if archive_tar.exists():
                archive_tar.unlink()
            
            logger.info(f"Clean source revision {commit_sha[:8]} exported to {target_dir}")
            return target_dir
        except Exception as e:
            if archive_tar.exists():
                archive_tar.unlink()
            raise GitRepositoryError(f"Failed to export clean revision {commit_sha}: {e}") from e
