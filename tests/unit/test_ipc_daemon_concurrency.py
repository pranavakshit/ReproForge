"""Concurrency tests for IPC daemon non-blocking heartbeats, live log streaming, and active build cancellation."""

import asyncio
import json
from pathlib import Path
import subprocess
import pytest

from backend.app.execution.runner import ACTIVE_BUILD_PROCESSES, LocalSandboxBackend
from backend.app.ipc.daemon import IpcDaemon
from backend.app.ipc.protocol import JsonRpcRequest
from backend.app.schemas.entities import BuildStatus


@pytest.mark.asyncio
async def test_local_sandbox_streaming_and_cancellation(tmp_path: Path):
    """Verify live line-by-line stdout chunk callback and active OS process cancellation."""
    source_dir = tmp_path / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    streamed_chunks = []

    runner = LocalSandboxBackend()
    cmd = 'python -u -c "import time; print(\'CHUNK_START\', flush=True); time.sleep(10); print(\'CHUNK_END\', flush=True)"'

    task = asyncio.create_task(
        runner.execute(
            build_id="bld-cancel-live",
            workspace_source_dir=source_dir,
            build_command=cmd,
            timeout_seconds=30,
            on_output_chunk=streamed_chunks.append,
        )
    )

    # Wait briefly until subprocess starts & first chunk streams before process finishes
    for _ in range(40):
        if "bld-cancel-live" in ACTIVE_BUILD_PROCESSES and any("CHUNK_START" in c for c in streamed_chunks):
            break
        await asyncio.sleep(0.05)

    assert "bld-cancel-live" in ACTIVE_BUILD_PROCESSES
    assert any("CHUNK_START" in c for c in streamed_chunks)

    from backend.app.execution.runner import terminate_build
    killed = await terminate_build("bld-cancel-live")
    assert killed is True

    result = await task
    assert result.status == BuildStatus.CANCELLED
    assert not any(c.strip() == "CHUNK_END" for c in streamed_chunks)


@pytest.mark.asyncio
async def test_daemon_concurrent_ping_not_blocked_by_build(tmp_path: Path):
    """Verify IpcDaemon schedules requests concurrently so system.ping replies while a build runs."""
    repo_dir = tmp_path / "concurrent_repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@reproforge.org"], cwd=repo_dir, check=True)
    (repo_dir / "pyproject.toml").write_text('[project]\nname="c"\nversion="0.1.0"\n', encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo_dir, check=True)

    daemon = IpcDaemon(log_to_file=False)
    messages = []
    daemon.write_ipc_message = lambda line: messages.append(json.loads(line))

    build_req = JsonRpcRequest(
        id=1,
        method="build.trigger",
        params={
            "build_id": "bld-slow-1",
            "repo_path": str(repo_dir),
            "backend_type": "local",
            "custom_cmd": 'python -u -c "import time; print(\'LIVE_STEP\', flush=True); time.sleep(0.6)"',
            "timeout_seconds": 15,
        },
    )
    ping_req = JsonRpcRequest(id=2, method="system.ping", params={"timestamp": 999.0})

    t_build = asyncio.create_task(daemon._dispatch_and_reply(build_req))
    await asyncio.sleep(0.1)
    t_ping = asyncio.create_task(daemon._dispatch_and_reply(ping_req))

    await t_ping
    ping_replies = [m for m in messages if m.get("id") == 2]
    assert len(ping_replies) == 1
    assert ping_replies[0]["result"]["status"] == "healthy"
    assert not t_build.done(), "Ping must complete before the slow build finishes"

    await t_build
    log_notifs = [m for m in messages if m.get("method") == "build.log_chunk" and m.get("params", {}).get("build_id") == "bld-slow-1"]
    assert any("LIVE_STEP" in n["params"]["chunk"] for n in log_notifs)
