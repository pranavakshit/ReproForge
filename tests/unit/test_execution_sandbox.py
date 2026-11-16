"""Unit tests for workspace management and isolated execution runner."""

from pathlib import Path
import pytest
from backend.app.execution.runner import LocalSandboxBackend
from backend.app.execution.workspace import WorkspaceManager
from backend.app.schemas.entities import BuildStatus


def test_workspace_allocation_and_cleanup(tmp_path: Path):
    """Verify workspace directory allocation and disposal."""
    ws_mgr = WorkspaceManager(base_workspaces_dir=tmp_path)
    ws_path = ws_mgr.allocate_workspace(build_id="test-build-1")

    assert ws_path.exists()
    assert (ws_path / "source").exists()

    ws_mgr.cleanup_workspace(ws_path)
    assert not ws_path.exists()


@pytest.mark.asyncio
async def test_local_sandbox_successful_execution(tmp_path: Path):
    """Verify local command execution, log streaming, and exit tracking."""
    source_dir = tmp_path / "source"
    source_dir.mkdir(parents=True, exist_ok=True)

    test_file = source_dir / "output.txt"
    runner = LocalSandboxBackend()

    # Create Python command writing to a file and printing stdout
    cmd = 'python -c "print(\'ReproForge Sandbox OK\'); open(\'output.txt\', \'w\').write(\'sample data\')"'

    result = await runner.execute(
        build_id="test-exec-1",
        workspace_source_dir=source_dir,
        build_command=cmd,
        timeout_seconds=10,
    )

    assert result.status == BuildStatus.COMPLETED
    assert result.exit_code == 0
    assert result.duration_seconds >= 0
    assert test_file.exists()
    assert test_file.read_text() == "sample data"

    log_path = Path(result.log_file_path)
    assert log_path.exists()
    log_content = log_path.read_text(encoding="utf-8")
    assert "ReproForge Sandbox OK" in log_content


@pytest.mark.asyncio
async def test_local_sandbox_timeout_handling(tmp_path: Path):
    """Verify timeout terminates long-running process with TIMED_OUT status."""
    source_dir = tmp_path / "source"
    source_dir.mkdir(parents=True, exist_ok=True)

    runner = LocalSandboxBackend()
    # Python sleep 5 seconds with a 1 second timeout
    cmd = 'python -c "import time; time.sleep(5)"'

    result = await runner.execute(
        build_id="test-timeout-1",
        workspace_source_dir=source_dir,
        build_command=cmd,
        timeout_seconds=1,
    )

    assert result.status == BuildStatus.TIMED_OUT
    assert result.exit_code == 124
