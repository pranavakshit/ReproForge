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


@pytest.mark.asyncio
async def test_local_sandbox_uses_repo_isolated_venv_not_host_venv(tmp_path: Path):
    """Verify repo builds run strictly inside their own workspace .venv rather than ReproForge's .venv."""
    import sys

    source_dir = tmp_path / "repo_a" / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    out_file = source_dir / "venv_probe.txt"

    runner = LocalSandboxBackend()
    cmd = 'python -c "import os, shutil, sys; open(\'venv_probe.txt\', \'w\').write(sys.prefix + \'|\' + os.environ.get(\'VIRTUAL_ENV\', \'\') + \'|\' + (shutil.which(\'python\') or \'\'))"'

    result = await runner.execute(
        build_id="test-venv-isolation",
        workspace_source_dir=source_dir,
        build_command=cmd,
        timeout_seconds=15,
    )

    assert result.status == BuildStatus.COMPLETED
    expected_venv = (source_dir / ".venv").resolve()
    assert expected_venv.exists()
    assert result.metadata["venv_path"] == str(expected_venv)

    probe_prefix, probe_env, probe_which = out_file.read_text(encoding="utf-8").split("|")
    assert Path(probe_prefix).resolve() == expected_venv
    assert Path(probe_env).resolve() == expected_venv
    assert Path(probe_prefix).resolve() != Path(sys.prefix).resolve()
    assert str(expected_venv).lower() in str(Path(probe_which).resolve()).lower()


@pytest.mark.asyncio
async def test_docker_backend_runs_container_and_pulls_logs_before_rm(tmp_path: Path, monkeypatch):
    """Verify DockerBuildBackend never creates host .venv, uses --network=none without --rm, and pulls docker logs + inspect before docker rm -f."""
    from backend.app.execution.runner import DockerBuildBackend

    source_dir = tmp_path / "no_dockerfile_repo" / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "main.py").write_text("print('hello')\n", encoding="utf-8")

    call_sequence = []

    monkeypatch.setattr("shutil.which", lambda name: "docker" if name == "docker" else None)
    monkeypatch.setattr(DockerBuildBackend, "_find_cached_python_image", staticmethod(lambda: "python:3.11-slim"))
    monkeypatch.setattr(DockerBuildBackend, "_image_exists_locally", staticmethod(lambda tag: True))

    async def fake_stream(build_id, cmd_or_args, cwd, env, log_fp, timeout_seconds, on_output_chunk=None, use_shell=True):
        call_sequence.append(("run", list(cmd_or_args)))
        log_fp.write("Container build stdout\n")
        return BuildStatus.COMPLETED, 0, None

    async def fake_pull_logs(ctr_name, log_fp, on_output_chunk=None):
        call_sequence.append(("logs_and_inspect", ctr_name))
        log_fp.write(f"Pulled log for {ctr_name}\n")
        return {"container_name": ctr_name, "exit_code": 0}

    async def fake_rm(ctr_name, log_fp):
        call_sequence.append(("rm", ctr_name))

    monkeypatch.setattr("backend.app.execution.runner._stream_subprocess_to_log", fake_stream)
    monkeypatch.setattr(DockerBuildBackend, "_pull_container_logs_and_inspect", staticmethod(fake_pull_logs))
    monkeypatch.setattr(DockerBuildBackend, "_remove_container", staticmethod(fake_rm))

    backend = DockerBuildBackend()
    result = await backend.execute(
        build_id="test-docker-1",
        workspace_source_dir=source_dir,
        build_command="python -m compileall .",
        timeout_seconds=15,
        dependency_mode="source_only",
    )

    assert result.status == BuildStatus.COMPLETED
    assert result.backend_type == "docker"
    assert result.metadata["isolation_tier"] == "container_ephemeral"
    assert not (source_dir / ".venv").exists()  # Never pollute Docker builds with host .venv

    run_args = call_sequence[0][1]
    assert "--rm" not in run_args
    assert "--network=none" in run_args
    assert "reproforge-ctr-test-docker-1" in run_args
    assert [step[0] for step in call_sequence] == ["run", "logs_and_inspect", "rm"]
