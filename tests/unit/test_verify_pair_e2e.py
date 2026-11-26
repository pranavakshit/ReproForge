import subprocess
from pathlib import Path
import pytest

from backend.app.ipc.dispatcher import RequestDispatcher
from backend.app.ipc.protocol import JsonRpcRequest


@pytest.mark.asyncio
async def test_track_repo_verify_pair_and_inspect_all_views_e2e(tmp_path: Path):
    """End-to-end verification of user workflow: add repo -> verify_pair -> comparison -> graph -> passport -> remediation."""
    repo_dir = tmp_path / "demo_app"
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "demo@reproforge.local"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Demo User"], cwd=repo_dir, check=True, capture_output=True)
    (repo_dir / "requirements.txt").write_text("click\n", encoding="utf-8")
    (repo_dir / "main.py").write_text("def greet():\n    return 'hello'\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial app"], cwd=repo_dir, check=True, capture_output=True)

    dispatcher = RequestDispatcher()

    # 1. Register repo
    reg = await dispatcher.dispatch(
        JsonRpcRequest(
            id=1,
            method="repository.register",
            params={"name": "demo_app", "repo_url": str(repo_dir), "repo_type": "local"},
        )
    )
    repo_id = reg.result["id"]
    assert reg.result["name"] == "demo_app"

    # 2. Verify reproducibility pair (2 isolated .venv builds + ComparisonService)
    verify = await dispatcher.dispatch(
        JsonRpcRequest(
            id=2,
            method="build.verify_pair",
            params={"repository_id": repo_id, "backend_type": "local"},
        )
    )
    assert verify.result["comparison_id"] is not None
    assert verify.result["verdict"] in {
        "BIT_FOR_BIT_REPRODUCIBLE",
        "NORMALIZED_REPRODUCIBLE",
        "PARTIALLY_REPRODUCIBLE",
        "NON_REPRODUCIBLE",
    }

    # 3. Diagnostics view query scoped to repository_id returns real float CRI metrics
    diag = await dispatcher.dispatch(
        JsonRpcRequest(id=3, method="comparison.get_latest", params={"repository_id": repo_id})
    )
    assert diag.result["id"] == verify.result["comparison_id"]
    assert isinstance(diag.result["content_agreement_ratio"], float)
    assert isinstance(diag.result["metadata_agreement_ratio"], float)

    # 4. Causality graph scoped to repository_id
    graph = await dispatcher.dispatch(
        JsonRpcRequest(id=4, method="causality.get_graph", params={"repository_id": repo_id})
    )
    assert len(graph.result["nodes"]) >= 3
    assert len(graph.result["edges"]) >= 2

    # 5. Reproducibility passport
    passport = await dispatcher.dispatch(
        JsonRpcRequest(id=5, method="passport.get", params={"repository_id": repo_id})
    )
    assert passport.result["passport"]["repository"]["id"] == repo_id
    assert len(passport.result["passport"]["builds"]) == 2

    # 6. Verify Overview, Build List & Revision List expose non-unknown populated display fields
    ov = await dispatcher.dispatch(JsonRpcRequest(id=6, method="overview.get_metrics", params={}))
    assert ov.result["recent_builds"][0]["repo"] == "demo_app"
    assert ov.result["recent_builds"][0]["revision"] != "HEAD"

    bl = await dispatcher.dispatch(JsonRpcRequest(id=7, method="build.list", params={"repository_id": repo_id}))
    assert bl.result["builds"][0]["build_id"]
    assert bl.result["builds"][0]["repo_name"] == "demo_app"
    assert bl.result["builds"][0]["primary_hash"].startswith("sha256:")

    rv = await dispatcher.dispatch(JsonRpcRequest(id=8, method="repository.get_revisions", params={"repository_id": repo_id}))
    assert rv.result["revisions"][0]["sha"] != "HEAD"
    assert rv.result["revisions"][0]["message"] == "initial app"
