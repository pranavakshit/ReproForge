"""Isolated build execution engine with pluggable execution backends."""

import asyncio
import os
import shutil
import sys
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Set
from pydantic import BaseModel

from backend.app.core.logging import get_logger
from backend.app.schemas.entities import BuildStatus

logger = get_logger("reproforge.execution.runner")

# Registry of live build subprocesses keyed by build_id for active cancellation
ACTIVE_BUILD_PROCESSES: Dict[str, asyncio.subprocess.Process] = {}
CANCELLED_BUILD_IDS: Set[str] = set()


async def terminate_build(build_id: str) -> bool:
    """Forcibly terminate an active build process tree if running."""
    CANCELLED_BUILD_IDS.add(build_id)
    proc = ACTIVE_BUILD_PROCESSES.get(build_id)
    if not proc or proc.returncode is not None:
        return False

    try:
        if sys.platform == "win32":
            kill_proc = await asyncio.create_subprocess_exec(
                "taskkill",
                "/F",
                "/T",
                "/PID",
                str(proc.pid),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await kill_proc.wait()
        else:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                proc.kill()
        logger.info(f"[{build_id}] Terminated active process PID={proc.pid}")
        return True
    except ProcessLookupError:
        return False
    except Exception as exc:
        logger.warning(f"[{build_id}] Failed terminating process PID={proc.pid}: {exc}")
        return False


class BuildExecutionResult(BaseModel):
    """Execution telemetry and exit metadata for a build run."""

    build_id: str
    backend_type: str
    status: BuildStatus
    exit_code: int
    started_at: datetime
    completed_at: datetime
    duration_seconds: float
    log_file_path: str
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = {}


class BaseBuildBackend(ABC):
    """Abstract interface for isolated build execution backends."""

    @abstractmethod
    async def execute(
        self,
        build_id: str,
        workspace_source_dir: Path,
        build_command: str,
        env_vars: Optional[Dict[str, str]] = None,
        timeout_seconds: int = 1200,
        on_output_chunk: Optional[Callable[[str], None]] = None,
    ) -> BuildExecutionResult:
        """Execute a build command inside the isolated environment."""
        pass


class LocalSandboxBackend(BaseBuildBackend):
    """Executes build commands in a local isolated workspace directory with strict timeouts."""

    async def execute(
        self,
        build_id: str,
        workspace_source_dir: Path,
        build_command: str,
        env_vars: Optional[Dict[str, str]] = None,
        timeout_seconds: int = 1200,
        on_output_chunk: Optional[Callable[[str], None]] = None,
    ) -> BuildExecutionResult:
        log_file = workspace_source_dir.parent / f"{build_id}.log"
        started_at = datetime.now(timezone.utc)
        start_time = time.monotonic()

        # Build clean environment dictionary
        clean_env = os.environ.copy()
        if env_vars:
            clean_env.update(env_vars)

        logger.info(f"[{build_id}] Starting local sandbox build: '{build_command}' in {workspace_source_dir}")

        status = BuildStatus.BUILDING
        exit_code = -1
        error_msg = None

        CANCELLED_BUILD_IDS.discard(build_id)

        with open(log_file, "w", encoding="utf-8") as log_fp:
            header = (
                f"=== ReproForge Build Log: {build_id} ===\n"
                f"Timestamp: {started_at.isoformat()}\n"
                f"Command: {build_command}\n"
                f"Working Directory: {workspace_source_dir}\n"
                f"=========================================\n\n"
            )
            log_fp.write(header)
            log_fp.flush()
            if on_output_chunk:
                on_output_chunk(header)

            proc: Optional[asyncio.subprocess.Process] = None
            try:
                proc = await asyncio.create_subprocess_shell(
                    build_command,
                    cwd=str(workspace_source_dir),
                    env=clean_env,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                ACTIVE_BUILD_PROCESSES[build_id] = proc

                async def stream_output(stream, prefix):
                    while True:
                        line = await stream.readline()
                        if not line:
                            break
                        decoded = line.decode("utf-8", errors="replace")
                        formatted = f"{prefix}{decoded}"
                        log_fp.write(formatted)
                        log_fp.flush()
                        if on_output_chunk:
                            try:
                                on_output_chunk(formatted)
                            except Exception:
                                pass

                try:
                    await asyncio.wait_for(
                        asyncio.gather(
                            stream_output(proc.stdout, ""),
                            stream_output(proc.stderr, "[STDERR] "),
                        ),
                        timeout=timeout_seconds,
                    )
                    await proc.wait()
                    if build_id in CANCELLED_BUILD_IDS:
                        exit_code = -1
                        status = BuildStatus.CANCELLED
                        error_msg = "Build cancelled by user"
                        log_fp.write("\n[CANCELLED] Build execution terminated cleanly.\n")
                    else:
                        exit_code = proc.returncode or 0
                        status = BuildStatus.COMPLETED if exit_code == 0 else BuildStatus.FAILED
                except asyncio.TimeoutError:
                    logger.error(f"[{build_id}] Build timed out after {timeout_seconds} seconds.")
                    await terminate_build(build_id)
                    CANCELLED_BUILD_IDS.discard(build_id)
                    status = BuildStatus.TIMED_OUT
                    exit_code = 124
                    error_msg = f"Build timed out after {timeout_seconds} seconds"
                    log_fp.write(f"\n[FATAL] {error_msg}\n")

            except Exception as e:
                if build_id in CANCELLED_BUILD_IDS:
                    status = BuildStatus.CANCELLED
                    exit_code = -1
                    error_msg = "Build cancelled by user"
                    log_fp.write("\n[CANCELLED] Build execution terminated cleanly.\n")
                else:
                    logger.error(f"[{build_id}] Unexpected execution failure: {e}")
                    status = BuildStatus.FAILED
                    exit_code = 1
                    error_msg = str(e)
                    log_fp.write(f"\n[EXCEPTION] {e}\n")
            finally:
                ACTIVE_BUILD_PROCESSES.pop(build_id, None)
                CANCELLED_BUILD_IDS.discard(build_id)

        end_time = time.monotonic()
        completed_at = datetime.now(timezone.utc)
        duration = round(end_time - start_time, 3)

        logger.info(f"[{build_id}] Finished with status={status.value}, exit_code={exit_code}, duration={duration}s")

        return BuildExecutionResult(
            build_id=build_id,
            backend_type="local_sandbox",
            status=status,
            exit_code=exit_code,
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=duration,
            log_file_path=str(log_file),
            error_message=error_msg,
            metadata={"command": build_command},
        )


class DockerBuildBackend(BaseBuildBackend):
    """Executes container builds using Docker Engine / BuildKit with strict caching disabled."""

    async def execute(
        self,
        build_id: str,
        workspace_source_dir: Path,
        build_command: str = "docker build --no-cache",
        env_vars: Optional[Dict[str, str]] = None,
        timeout_seconds: int = 1200,
        on_output_chunk: Optional[Callable[[str], None]] = None,
    ) -> BuildExecutionResult:
        docker_bin = shutil.which("docker")
        if not docker_bin:
            raise RuntimeError("Docker executable not found on system PATH.")

        image_tag = f"reproforge-{build_id}:target"
        # Force --no-cache to ensure pure build reproduction from source
        full_command = f"docker build --no-cache -t {image_tag} ."

        # Delegate execution to local sandbox running docker CLI
        sandbox = LocalSandboxBackend()
        result = await sandbox.execute(
            build_id=build_id,
            workspace_source_dir=workspace_source_dir,
            build_command=full_command,
            env_vars=env_vars,
            timeout_seconds=timeout_seconds,
            on_output_chunk=on_output_chunk,
        )
        result.backend_type = "docker"
        result.metadata["image_tag"] = image_tag
        return result
