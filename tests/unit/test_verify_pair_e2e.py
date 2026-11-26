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
    captured_notifications = []
    dispatcher.on_notify = lambda m, p: captured_notifications.append((m, p))

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

    # 2. Verify reproducibility pair (with source_only dependency_mode check + progress build_id scoping)
    verify = await dispatcher.dispatch(
        JsonRpcRequest(
            id=2,
            method="build.verify_pair",
            params={"repository_id": repo_id, "backend_type": "local", "dependency_mode": "source_only"},
        )
    )
    assert verify.result["comparison_id"] is not None
    progress_events = [p for m, p in captured_notifications if m == "build.progress"]
    assert progress_events and all(pe.get("build_id") for pe in progress_events)
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

    # 7. CLI Parity IPC routes: fingerprint.capture, comparison.compare_paths, comparison.export_report
    fp_res = await dispatcher.dispatch(JsonRpcRequest(id=9, method="fingerprint.capture", params={}))
    assert fp_res.result["fingerprint_hash"]
    assert "system_info" in fp_res.result["json_pretty"]

    f1 = tmp_path / "art_1.txt"
    f2 = tmp_path / "art_2.txt"
    f1.write_text("identical bytes", encoding="utf-8")
    f2.write_text("identical bytes", encoding="utf-8")
    cmp_paths = await dispatcher.dispatch(
        JsonRpcRequest(id=10, method="comparison.compare_paths", params={"path_a": str(f1), "path_b": str(f2)})
    )
    assert cmp_paths.result["verdict"] == "BIT_FOR_BIT_REPRODUCIBLE"

    exp_res = await dispatcher.dispatch(
        JsonRpcRequest(
            id=11,
            method="comparison.export_report",
            params={"comparison_id": verify.result["comparison_id"], "format": "html"},
        )
    )
    assert Path(exp_res.result["file_path"]).is_file()
    assert exp_res.result["bytes"] > 100
