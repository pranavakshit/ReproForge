import subprocess
from pathlib import Path
import pytest

from backend.app.database.session import AsyncSessionLocal
from backend.app.diagnostics.evidence import EpistemicStatus
from backend.app.models.entities import (
    BuildComparisonModel,
    BuildExecutionModel,
    DiagnosticFindingModel,
    ProjectModel,
    RepositoryModel,
    SourceRevisionModel,
)
from backend.app.schemas.entities import ConfidenceLevel, DiagnosticCategory
from backend.app.services.perturbation_service import PerturbationService


def _init_temp_git_repo(repo_dir: Path) -> None:
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@reproforge.local"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "ReproForge Test"], cwd=repo_dir, check=True, capture_output=True)
    (repo_dir / "pyproject.toml").write_text('[project]\nname = "demo-pkg"\nversion = "0.1.0"\n', encoding="utf-8")
    (repo_dir / "demo.py").write_text("VALUE = 42\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=repo_dir, check=True, capture_output=True)


def test_perturbation_confounder_guard():
    base = {"SOURCE_DATE_EPOCH": "1700000000", "TZ": "UTC", "UMASK": "0022"}
    good_arm = {"SOURCE_DATE_EPOCH": "1760000000", "TZ": "UTC", "UMASK": "0022"}
    PerturbationService.validate_single_variable_delta(base, good_arm, "SOURCE_DATE_EPOCH")

    confounded_arm = {"SOURCE_DATE_EPOCH": "1760000000", "TZ": "Asia/Tokyo", "UMASK": "0022"}
    with pytest.raises(ValueError, match="Confounder guard"):
        PerturbationService.validate_single_variable_delta(base, confounded_arm, "SOURCE_DATE_EPOCH")


@pytest.mark.asyncio
async def test_perturbation_campaign_promotes_finding_epistemic_status(tmp_path: Path):
    repo_dir = tmp_path / "perturb_demo_repo"
    _init_temp_git_repo(repo_dir)

    async with AsyncSessionLocal() as session:
        proj = ProjectModel(id="proj-p1", name="PerturbProj")
        repo = RepositoryModel(
            id="repo-p1",
            project_id="proj-p1",
            name="perturb_demo_repo",
            repo_url=str(repo_dir),
            repo_type="local",
            default_branch="main",
        )
        rev = SourceRevisionModel(
            id="rev-p1",
            repository_id="repo-p1",
            commit_sha="a" * 40,
            branch_name="main",
            author_name="Tester",
            commit_message="initial",
        )
        b1 = BuildExecutionModel(
            id="bld-p1",
            repository_id="repo-p1",
            revision_id="rev-p1",
            profile_name="local",
            status="succeeded",
        )
        b2 = BuildExecutionModel(
            id="bld-p2",
            repository_id="repo-p1",
            revision_id="rev-p1",
            profile_name="local",
            status="succeeded",
        )
        comp = BuildComparisonModel(
            id="cmp-p1",
            build_id_1="bld-p1",
            build_id_2="bld-p2",
            mode="normalized",
            exact_match=False,
            normalized_match=False,
        )
        finding = DiagnosticFindingModel(
            id="fnd-p1",
            comparison_id="cmp-p1",
            category=DiagnosticCategory.TIMESTAMP_NONDETERMINISM.value,
            confidence=ConfidenceLevel.MEDIUM.value,
            affected_paths=["dist/demo.tar"],
            root_cause_summary="Suspected timestamp embed in archive mtimes",
            suggested_remediation="Pin SOURCE_DATE_EPOCH",
            evidence_trail=[],
        )
        session.add(proj)
        await session.flush()
        session.add(repo)
        await session.flush()
        session.add(rev)
        await session.flush()
        session.add_all([b1, b2])
        await session.flush()
        session.add(comp)
        await session.flush()
        session.add(finding)
        await session.commit()

    service = PerturbationService()
    campaign = await service.run_campaign(
        repository_id="repo-p1",
        param_names=["SOURCE_DATE_EPOCH", "TZ"],
        execute_real_builder=False,
    )

    assert len(campaign["results"]) == 2
    epoch_res = next(r for r in campaign["results"] if r["param_name"] == "SOURCE_DATE_EPOCH")
    assert epoch_res["hash_changed"] is True
    assert epoch_res["epistemic_status"] == "EXPERIMENTALLY_VALIDATED"
    assert epoch_res["promoted_findings_count"] >= 1

    async with AsyncSessionLocal() as session:
        refreshed = await session.get(DiagnosticFindingModel, "fnd-p1")
        assert refreshed is not None
        assert refreshed.confidence == ConfidenceLevel.HIGH.value
        assert EpistemicStatus.EXPERIMENTALLY_VALIDATED.value in str(refreshed.evidence_trail).lower()

    # Verify localized Asia/Kolkata TZ preset and custom param_overrides filtering
    defaults = {p["name"]: p for p in PerturbationService.list_default_parameters()}
    assert defaults["TZ"]["perturbed_val"] == "Asia/Kolkata"

    override_camp = await service.run_campaign(
        repository_id="repo-p1",
        param_overrides=[
            {"name": "SOURCE_DATE_EPOCH", "baseline_val": "1700000000", "perturbed_val": "1710000000", "enabled": True},
            {"name": "TZ", "baseline_val": "UTC", "perturbed_val": "Asia/Kolkata", "enabled": False},
            {"name": "LC_ALL", "enabled": False},
            {"name": "UMASK", "enabled": False},
            {"name": "PYTHONHASHSEED", "enabled": False},
        ],
        execute_real_builder=False,
    )
    assert len(override_camp["results"]) == 1
    assert override_camp["results"][0]["param_name"] == "SOURCE_DATE_EPOCH"
    assert override_camp["results"][0]["hash_changed"] is True
