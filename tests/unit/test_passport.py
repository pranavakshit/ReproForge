import json
from pathlib import Path
import pytest

from backend.app.database.session import AsyncSessionLocal
from backend.app.models.entities import (
    ArtifactItemModel,
    BuildComparisonModel,
    BuildExecutionModel,
    DiagnosticFindingModel,
    ProjectModel,
    RepositoryModel,
    ReproducibilityScoreModel,
    SourceRevisionModel,
)
from backend.app.schemas.entities import ConfidenceLevel, DiagnosticCategory, ReproducibilityVerdict
from backend.app.services.passport_service import PassportService


def test_passport_secret_redaction():
    raw = "https://alice:ghp_SuperSecretToken12345@github.com/alice/private.git"
    sanitized = PassportService.sanitize_text(raw)
    assert "ghp_SuperSecretToken12345" not in sanitized
    assert "alice:" not in sanitized
    assert "[REDACTED]" in sanitized

    env = {"GITHUB_TOKEN": "ghp_abc123xyz456", "TZ": "UTC", "DB_PASSWORD": "secret"}
    clean_env = PassportService.sanitize_env(env)
    assert clean_env["GITHUB_TOKEN"] == "[REDACTED]"
    assert clean_env["DB_PASSWORD"] == "[REDACTED]"
    assert clean_env["TZ"] == "UTC"


@pytest.mark.asyncio
async def test_passport_generation_and_export(tmp_path: Path):
    async with AsyncSessionLocal() as session:
        proj = ProjectModel(id="proj-pass", name="PassProj")
        repo = RepositoryModel(
            id="repo-pass",
            project_id="proj-pass",
            name="PassRepo",
            repo_url="https://user:secretpass@github.com/pranavakshit/ReproForge.git",
            repo_type="remote",
            default_branch="main",
            upstream_github_url="https://github.com/pranavakshit/ReproForge",
            github_slug="pranavakshit/ReproForge",
        )
        rev = SourceRevisionModel(
            id="rev-pass-1",
            repository_id="repo-pass",
            commit_sha="abcdef123456" * 3 + "abcd",
            branch_name="main",
            author_name="Tester",
            commit_message="commit",
        )
        b1 = BuildExecutionModel(
            id="bld-pass-1",
            repository_id="repo-pass",
            revision_id="rev-pass-1",
            profile_name="docker",
            status="succeeded",
        )
        art = ArtifactItemModel(
            id="art-pass-1",
            build_execution_id="bld-pass-1",
            relative_path="dist/demo.whl",
            artifact_type="wheel",
            strict_sha256="a" * 64,
            file_size_bytes=1234,
        )
        comp = BuildComparisonModel(
            id="cmp-pass-1",
            build_id_1="bld-pass-1",
            build_id_2="bld-pass-1",
            mode="strict",
            exact_match=True,
            normalized_match=True,
        )
        score = ReproducibilityScoreModel(
            id="scr-pass-1",
            comparison_id="cmp-pass-1",
            exact_match=True,
            normalized_match=True,
            content_agreement_ratio=1.0,
            metadata_agreement_ratio=1.0,
            environment_consistency_score=1.0,
            composite_reproducibility_index=1.0,
            verdict=ReproducibilityVerdict.BIT_FOR_BIT_REPRODUCIBLE.value,
        )
        finding = DiagnosticFindingModel(
            id="fnd-pass-1",
            comparison_id="cmp-pass-1",
            category=DiagnosticCategory.TIMESTAMP_NONDETERMINISM.value,
            confidence=ConfidenceLevel.HIGH.value,
            affected_paths=["dist/demo.whl"],
            root_cause_summary="Header synced cleanly with token ghp_LeakAttempt0001",
            suggested_remediation="Keep pinned epoch",
            evidence_trail=[{"epistemic_status": "observed"}],
        )
        session.add(proj)
        await session.flush()
        session.add(repo)
        await session.flush()
        session.add(rev)
        await session.flush()
        session.add(b1)
        await session.flush()
        session.add_all([art, comp])
        await session.flush()
        session.add_all([score, finding])
        await session.commit()

    service = PassportService()
    json_res = await service.export_passport("repo-pass", fmt="json")
    md_res = await service.export_passport("repo-pass", fmt="markdown")

    assert Path(json_res["file_path"]).exists()
    assert Path(md_res["file_path"]).exists()

    parsed = json.loads(json_res["content"])
    assert parsed["passport_format"] == "reproforge-passport-v1.0"
    assert parsed["verification_status"] == "VERIFIED_BIT_FOR_BIT"
    assert parsed["passport_digest"].startswith("sha256:")
    assert "secretpass" not in json_res["content"]
    assert "ghp_LeakAttempt0001" not in json_res["content"]
    assert "ReproForge Reproducibility Passport" in md_res["content"]

    from backend.app.ipc.dispatcher import RequestDispatcher
    from backend.app.ipc.protocol import JsonRpcRequest

    dispatcher = RequestDispatcher()
    p_resp = await dispatcher.dispatch(JsonRpcRequest(id=1, method="perturbation.params", params={}))
    assert len(p_resp.result["params"]) == 5

    g_resp = await dispatcher.dispatch(JsonRpcRequest(id=2, method="passport.get", params={"repository_id": "repo-pass"}))
    assert g_resp.result["passport"]["repository"]["id"] == "repo-pass"

    r_resp = await dispatcher.dispatch(JsonRpcRequest(id=3, method="remediation.list_proposals", params={"repository_id": "repo-pass"}))
    assert len(r_resp.result["proposals"]) >= 1
