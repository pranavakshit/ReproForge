import subprocess
from pathlib import Path
import pytest

from backend.app.database.session import AsyncSessionLocal
from backend.app.models.entities import (
    BuildComparisonModel,
    BuildExecutionModel,
    DiagnosticFindingModel,
    ProjectModel,
    RepositoryModel,
    SourceRevisionModel,
)
from backend.app.schemas.entities import ConfidenceLevel, DiagnosticCategory
from backend.app.services.remediation_engine import (
    ApprovalRequiredError,
    DiagnosisOnlyPolicyError,
    RemediationEngine,
)


def _init_repo(repo_dir: Path) -> None:
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@reproforge.local"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo_dir, check=True, capture_output=True)
    (repo_dir / "app.py").write_text("print('hello')\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo_dir, check=True, capture_output=True)


@pytest.mark.asyncio
async def test_remediation_three_tier_policy_and_disposable_verification(tmp_path: Path):
    repo_dir = tmp_path / "rem_repo"
    _init_repo(repo_dir)

    async with AsyncSessionLocal() as session:
        proj = ProjectModel(id="proj-r1", name="RemProj")
        repo = RepositoryModel(
            id="repo-r1",
            project_id="proj-r1",
            name="rem_repo",
            repo_url=str(repo_dir),
            repo_type="local",
            default_branch="main",
        )
        rev = SourceRevisionModel(
            id="rev-r1",
            repository_id="repo-r1",
            commit_sha="b" * 40,
            branch_name="main",
            author_name="Tester",
            commit_message="init",
        )
        b1 = BuildExecutionModel(
            id="bld-r1",
            repository_id="repo-r1",
            revision_id="rev-r1",
            profile_name="local",
            status="succeeded",
        )
        comp = BuildComparisonModel(
            id="cmp-r1",
            build_id_1="bld-r1",
            build_id_2="bld-r1",
            mode="normalized",
            exact_match=False,
            normalized_match=False,
        )
        f_l1 = DiagnosticFindingModel(
            id="fnd-l1",
            comparison_id="cmp-r1",
            category=DiagnosticCategory.TIMESTAMP_NONDETERMINISM.value,
            confidence=ConfidenceLevel.HIGH.value,
            affected_paths=["dist/app.tar"],
            root_cause_summary="Timestamp variance",
            suggested_remediation="Pin SOURCE_DATE_EPOCH",
            evidence_trail=[{"epistemic_status": "observed"}],
        )
        f_l2 = DiagnosticFindingModel(
            id="fnd-l2",
            comparison_id="cmp-r1",
            category=DiagnosticCategory.BASE_IMAGE_VARIATION.value,
            confidence=ConfidenceLevel.HIGH.value,
            affected_paths=["Dockerfile"],
            root_cause_summary="Mutable base image tag",
            suggested_remediation="Pin digest",
            evidence_trail=[{"epistemic_status": "inferred"}],
        )
        f_l3 = DiagnosticFindingModel(
            id="fnd-l3",
            comparison_id="cmp-r1",
            category=DiagnosticCategory.GENERATED_CONTENT.value,
            confidence=ConfidenceLevel.LOW.value,
            affected_paths=["gen.py"],
            root_cause_summary="Random generated code",
            suggested_remediation="Manual refactor",
            evidence_trail=[{"epistemic_status": "hypothesis"}],
        )
        session.add(proj)
        await session.flush()
        session.add(repo)
        await session.flush()
        session.add(rev)
        await session.flush()
        session.add(b1)
        await session.flush()
        session.add(comp)
        await session.flush()
        session.add_all([f_l1, f_l2, f_l3])
        await session.commit()

    engine = RemediationEngine()
    proposals = await engine.list_proposals("repo-r1")
    tiers = {p["tier"]: p for p in proposals}
    assert "LEVEL_1" in tiers
    assert "LEVEL_2" in tiers
    assert "LEVEL_3" in tiers

    # 1. Level 3 must strictly block automated patching
    with pytest.raises(DiagnosisOnlyPolicyError):
        await engine.approve_and_verify(tiers["LEVEL_3"]["id"], repository_id="repo-r1", approved=True)

    # 2. Level 2 must reject without explicit user approval
    with pytest.raises(ApprovalRequiredError):
        await engine.approve_and_verify(tiers["LEVEL_2"]["id"], repository_id="repo-r1", approved=False)

    # 3. Level 1 & approved Level 2 execute inside isolated disposable copies without mutating original repo
    l1_res = await engine.approve_and_verify(tiers["LEVEL_1"]["id"], repository_id="repo-r1", approved=False)
    assert l1_res["verified"] is True
    assert l1_res["status"] == "VERIFIED"
    assert l1_res["digest_a"] == l1_res["digest_b"]
    assert not (repo_dir / ".reproforge.env").exists()

    l2_res = await engine.approve_and_verify(tiers["LEVEL_2"]["id"], repository_id="repo-r1", approved=True)
    assert l2_res["verified"] is True
    assert l2_res["status"] == "VERIFIED"
