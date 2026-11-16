"""Unit tests for Git repository manager and commit resolution."""

from pathlib import Path
import pytest
from backend.app.orchestration.git import GitRepositoryManager, GitRepositoryError


def test_git_manager_initialization():
    """Verify Git manager locates git binary."""
    mgr = GitRepositoryManager()
    assert mgr.git_binary == "git"


def test_resolve_commit_sha():
    """Verify commit SHA resolution on current repository."""
    mgr = GitRepositoryManager()
    repo_root = Path(__file__).resolve().parent.parent.parent
    sha = mgr.resolve_commit_sha(repo_root, "HEAD")
    assert len(sha) == 40
    assert all(c in "0123456789abcdefABCDEF" for c in sha)


def test_get_commit_metadata():
    """Verify commit metadata extraction."""
    mgr = GitRepositoryManager()
    repo_root = Path(__file__).resolve().parent.parent.parent
    meta = mgr.get_commit_metadata(repo_root, repository_id="test-repo-123", ref="HEAD")

    assert meta.commit_sha != ""
    assert len(meta.commit_sha) == 40
    assert meta.author_name != ""
    assert meta.committed_at is not None
    assert meta.commit_message != ""


def test_export_clean_revision(tmp_path):
    """Verify exporting clean detached revision without .git folder."""
    mgr = GitRepositoryManager()
    repo_root = Path(__file__).resolve().parent.parent.parent
    sha = mgr.resolve_commit_sha(repo_root, "HEAD")

    dest_dir = tmp_path / "clean_export"
    mgr.export_clean_revision(repo_root, dest_dir, sha)

    assert dest_dir.exists()
    assert not (dest_dir / ".git").exists()
    assert (dest_dir / "pyproject.toml").exists()
