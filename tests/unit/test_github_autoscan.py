"""Unit tests for GitHub remote detection, workspace checklist discovery, and CI readiness autoscan."""

import subprocess
from pathlib import Path
import pytest

from backend.app.orchestration.detector import BuildConfigurationDetector
from backend.app.orchestration.git import GitRepositoryManager
from backend.app.orchestration.github_scanner import GitHubAutoScanner
from backend.app.services.repository_service import RepositoryService


def test_parse_github_remote_urls():
    """Verify SSH and HTTPS GitHub URLs normalize cleanly to slug and canonical URL."""
    assert GitRepositoryManager.parse_github_remote_url("git@github.com:pranav7404/ReproForge.git") == (
        "pranav7404/ReproForge",
        "https://github.com/pranav7404/ReproForge",
    )
    assert GitRepositoryManager.parse_github_remote_url("https://github.com/pranav7404/ReproForge.git") == (
        "pranav7404/ReproForge",
        "https://github.com/pranav7404/ReproForge",
    )
    assert GitRepositoryManager.parse_github_remote_url("https://gitlab.com/other/repo.git") is None


def test_detector_rust_go_and_github_actions(tmp_path: Path):
    """Verify expanded detector handles Rust, Go, and GitHub Actions CI pin checks."""
    repo = tmp_path / "multi_repo"
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / "Cargo.toml").write_text('[package]\nname="app"\nversion="0.1.0"\n', encoding="utf-8")
    (repo / "Cargo.lock").write_text("# lock", encoding="utf-8")
    (repo / ".github" / "workflows" / "ci.yml").write_text(
        "jobs:\n  build:\n    steps:\n      - uses: actions/checkout@v4\n",
        encoding="utf-8",
    )

    detector = BuildConfigurationDetector()
    systems = detector.detect(repo)
    types = {s.system_type for s in systems}
    assert {"rust", "github_actions"}.issubset(types)

    audit = detector.audit_reproducibility_readiness(repo)
    assert audit["has_lockfile"] is True
    assert len(audit["unpinned_github_actions"]) == 1
    assert audit["readiness_status"] == "warnings_detected"


@pytest.mark.asyncio
async def test_local_repo_autoscan_github_and_workspace_checklist(tmp_path: Path):
    """Verify single repo autoscan detects linked GitHub remote & parent folder discovers child checklist."""
    parent = tmp_path / "workspace"
    repo_a = parent / "repo_a"
    repo_b = parent / "repo_b"
    repo_a.mkdir(parents=True)
    repo_b.mkdir(parents=True)

    for repo_dir, remote in [
        (repo_a, "https://github.com/pranav7404/ReproForge.git"),
        (repo_b, "git@github.com:pranav7404/LocAi.git"),
    ]:
        subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo_dir, check=True)
        subprocess.run(["git", "config", "user.email", "tester@reproforge.org"], cwd=repo_dir, check=True)
        subprocess.run(["git", "remote", "add", "origin", remote], cwd=repo_dir, check=True)
        (repo_dir / "pyproject.toml").write_text('[project]\nname="x"\nversion="0.1.0"\n', encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
        subprocess.run(["git", "commit", "-m", "init repo"], cwd=repo_dir, check=True)

    svc = RepositoryService()
    discovery = await svc.discover_folder_candidates(parent)
    assert discovery["mode"] == "workspace_checklist"
    assert len(discovery["candidates"]) == 2
    slugs = {c["github_slug"] for c in discovery["candidates"]}
    assert slugs == {"pranav7404/ReproForge", "pranav7404/LocAi"}

    # Register checked item with offline metadata check
    registered = await svc.register_repository(
        name="repo_a",
        repo_url=str(repo_a),
        autoscan=True,
        fetch_online_metadata=False,
    )
    assert registered.github_slug == "pranav7404/ReproForge"
    assert registered.upstream_github_url == "https://github.com/pranav7404/ReproForge"
    assert registered.scan_metadata_json.get("readiness", {}).get("primary_build_system") == "python"
    revs = await svc.get_revisions(registered.id)
    assert len(revs) == 1
