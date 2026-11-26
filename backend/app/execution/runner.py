"""Isolated build execution engine with separated Docker Container and Local .venv Sandbox backends."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Union
from pydantic import BaseModel

from backend.app.core.config import settings
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


async def _stream_subprocess_to_log(
    build_id: str,
    cmd_or_args: Union[str, List[str]],
    cwd: Path,
    env: Optional[Dict[str, str]],
    log_fp,
    timeout_seconds: int,
    on_output_chunk: Optional[Callable[[str], None]] = None,
    use_shell: bool = True,
) -> tuple[BuildStatus, int, Optional[str]]:
    """Shared async subprocess runner supporting live log streaming, cancellation, and timeout."""
    status = BuildStatus.BUILDING
    exit_code = -1
    error_msg: Optional[str] = None
    CANCELLED_BUILD_IDS.discard(build_id)

    try:
        if use_shell:
            cmd_str = cmd_or_args if isinstance(cmd_or_args, str) else subprocess.list2cmdline(cmd_or_args)
            proc = await asyncio.create_subprocess_shell(
                cmd_str,
                cwd=str(cwd),
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        else:
            assert isinstance(cmd_or_args, list), "argv list required when use_shell=False"
            proc = await asyncio.create_subprocess_exec(
                *cmd_or_args,
                cwd=str(cwd),
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

        ACTIVE_BUILD_PROCESSES[build_id] = proc

        async def stream_output(stream, prefix: str) -> None:
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
                msg = "\n[CANCELLED] Build execution terminated cleanly.\n"
                log_fp.write(msg)
                if on_output_chunk:
                    on_output_chunk(msg)
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
            msg = f"\n[FATAL] {error_msg}\n"
            log_fp.write(msg)
            if on_output_chunk:
                on_output_chunk(msg)

    except Exception as exc:
        if build_id in CANCELLED_BUILD_IDS:
            status = BuildStatus.CANCELLED
            exit_code = -1
            error_msg = "Build cancelled by user"
            log_fp.write("\n[CANCELLED] Build execution terminated cleanly.\n")
        else:
            logger.error(f"[{build_id}] Unexpected execution failure: {exc}")
            status = BuildStatus.FAILED
            exit_code = 1
            error_msg = str(exc)
            log_fp.write(f"\n[EXCEPTION] {exc}\n")
    finally:
        ACTIVE_BUILD_PROCESSES.pop(build_id, None)
        CANCELLED_BUILD_IDS.discard(build_id)

    return status, exit_code, error_msg


def _compute_wheelhouse_dir(workspace_source_dir: Path, platform_tag: str) -> Optional[Path]:
    """Compute cache directory under data/wheelhouses/<platform>/<sha> when requirements.txt exists."""
    req_file = workspace_source_dir / "requirements.txt"
    if not req_file.is_file():
        return None
    digest = hashlib.sha256(req_file.read_bytes()).hexdigest()[:16]
    return (settings.DATA_DIR / "wheelhouses" / platform_tag / digest).resolve()


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
        dependency_mode: str = "full",
    ) -> BuildExecutionResult:
        """Execute a build command inside the isolated environment."""
        pass


class LocalSandboxBackend(BaseBuildBackend):
    """Executes build commands on host in an isolated per-build .venv with allowlisted env vars."""

    @staticmethod
    def _resolve_base_python() -> str:
        """Locate base Python outside ReproForge's own .venv to create clean isolated repo virtualenvs."""
        base = Path(sys.base_prefix)
        candidates = (
            [base / "python.exe", base / "Scripts" / "python.exe"]
            if os.name == "nt"
            else [base / "bin" / "python3", base / "bin" / "python"]
        )
        for cand in candidates:
            if cand.is_file():
                return str(cand)
        return sys.executable

    @classmethod
    def _ensure_workspace_venv(cls, workspace_source_dir: Path, include_pip: bool) -> tuple[Path, Path, Path]:
        """Create (or reuse within the same build workspace) an isolated .venv for the build."""
        venv_dir = (workspace_source_dir / ".venv").resolve()
        venv_bin = venv_dir / ("Scripts" if os.name == "nt" else "bin")
        venv_python = venv_bin / ("python.exe" if os.name == "nt" else "python")
        if not venv_python.exists():
            base_py = cls._resolve_base_python()
            cmd = [base_py, "-m", "venv"]
            if not include_pip:
                cmd.append("--without-pip")
            cmd.append(str(venv_dir))
            subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
        return venv_dir, venv_bin, venv_python

    @staticmethod
    def _build_isolated_env(
        venv_dir: Path,
        venv_bin: Path,
        env_vars: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        """Build allowlisted sandbox env pointing strictly to the build's .venv (never ReproForge's .venv)."""
        allowed_keys = {
            "PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "OS",
            "TEMP", "TMP", "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
            "APPDATA", "LOCALAPPDATA", "LANG", "LC_ALL", "TZ",
            "CARGO_HOME", "RUSTUP_HOME", "GOPATH", "GOROOT",
            "NPM_CONFIG_CACHE", "SOURCE_DATE_EPOCH", "PYTHONHASHSEED",
            "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH",
        }
        clean_env = {k: v for k, v in os.environ.items() if k.upper() in allowed_keys}

        host_venv_prefix = str(Path(sys.prefix).resolve()).lower()
        host_venv_env = os.environ.get("VIRTUAL_ENV", "").strip()
        host_venv_norm = str(Path(host_venv_env).resolve()).lower() if host_venv_env else ""
        path_key = "Path" if ("Path" in clean_env and "PATH" not in clean_env) else "PATH"
        raw_path = clean_env.get(path_key, "")
        filtered_paths = []
        for entry in raw_path.split(os.pathsep):
            if not entry:
                continue
            low = entry.lower()
            if (host_venv_prefix != str(Path(sys.base_prefix).resolve()).lower() and host_venv_prefix in low) or (
                host_venv_norm and host_venv_norm in low
            ):
                continue
            filtered_paths.append(entry)

        clean_env.pop("Path", None)
        clean_env["PATH"] = os.pathsep.join([str(venv_bin), *filtered_paths])
        clean_env["VIRTUAL_ENV"] = str(venv_dir)
        clean_env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"

        if env_vars:
            for k, v in env_vars.items():
                if k.upper() in {"VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME"}:
                    continue
                clean_env[k] = v
        return clean_env

    @staticmethod
    async def _prepare_host_wheelhouse(
        build_id: str,
        workspace_source_dir: Path,
        venv_python: Path,
        clean_env: Dict[str, str],
        log_fp,
        on_output_chunk: Optional[Callable[[str], None]] = None,
    ) -> Optional[Path]:
        """Cache requirements.txt wheels once on host for offline .venv installs."""
        wh_dir = _compute_wheelhouse_dir(workspace_source_dir, f"host-py{sys.version_info.major}{sys.version_info.minor}")
        if not wh_dir:
            return None
        ready_marker = wh_dir / ".ready"
        if ready_marker.is_file():
            note = f"[Wheelhouse Cache] Reusing host wheelhouse: {wh_dir}\n"
            log_fp.write(note)
            if on_output_chunk:
                on_output_chunk(note)
            return wh_dir

        wh_dir.mkdir(parents=True, exist_ok=True)
        prep_msg = f"[Wheelhouse Prep] Caching requirements.txt wheels once into {wh_dir}...\n"
        log_fp.write(prep_msg)
        if on_output_chunk:
            on_output_chunk(prep_msg)

        prep_cmd = [
            str(venv_python),
            "-m",
            "pip",
            "download",
            "-r",
            str(workspace_source_dir / "requirements.txt"),
            "-d",
            str(wh_dir),
        ]
        st, rc, _ = await _stream_subprocess_to_log(
            build_id=f"{build_id}-whprep",
            cmd_or_args=prep_cmd,
            cwd=workspace_source_dir,
            env=clean_env,
            log_fp=log_fp,
            timeout_seconds=600,
            on_output_chunk=on_output_chunk,
            use_shell=False,
        )
        if st == BuildStatus.COMPLETED and rc == 0:
            ready_marker.write_text("ok\n", encoding="utf-8")
            return wh_dir
        shutil.rmtree(wh_dir, ignore_errors=True)
        raise RuntimeError(f"Host dependency wheelhouse prep failed (status={st.value}, exit={rc})")

    async def execute(
        self,
        build_id: str,
        workspace_source_dir: Path,
        build_command: str,
        env_vars: Optional[Dict[str, str]] = None,
        timeout_seconds: int = 1200,
        on_output_chunk: Optional[Callable[[str], None]] = None,
        dependency_mode: str = "full",
    ) -> BuildExecutionResult:
        log_file = workspace_source_dir.parent / f"{build_id}.log"
        started_at = datetime.now(timezone.utc)

        has_req = (workspace_source_dir / "requirements.txt").is_file()
        needs_pip = any(
            (workspace_source_dir / marker).exists()
            for marker in ("pyproject.toml", "setup.py", "requirements.txt", "Pipfile", "poetry.lock")
        ) or any(tok in build_command.lower() for tok in ("pip", " -m build", "pytest"))

        venv_dir, venv_bin, venv_python = await asyncio.to_thread(
            self._ensure_workspace_venv,
            workspace_source_dir,
            needs_pip,
        )
        clean_env = self._build_isolated_env(venv_dir, venv_bin, env_vars)
        start_time = time.monotonic()

        logger.info(
            f"[{build_id}] Starting local sandbox build in isolated .venv ({venv_dir}): "
            f"'{build_command}' in {workspace_source_dir}"
        )

        status = BuildStatus.BUILDING
        exit_code = -1
        error_msg: Optional[str] = None

        with open(log_file, "w", encoding="utf-8") as log_fp:
            header = (
                f"=== ReproForge Build Log: {build_id} ===\n"
                f"Timestamp: {started_at.isoformat()}\n"
                f"Isolation: Local Host Sandbox + Isolated .venv ({venv_dir})\n"
                f"Dependency Mode: {dependency_mode}\n"
                f"Python: {venv_python}\n"
                f"Command: {build_command}\n"
                f"Working Directory: {workspace_source_dir}\n"
                f"=========================================\n\n"
            )
            log_fp.write(header)
            log_fp.flush()
            if on_output_chunk:
                on_output_chunk(header)

            if dependency_mode == "full" and has_req:
                try:
                    wh_dir = await self._prepare_host_wheelhouse(
                        build_id=build_id,
                        workspace_source_dir=workspace_source_dir,
                        venv_python=venv_python,
                        clean_env=clean_env,
                        log_fp=log_fp,
                        on_output_chunk=on_output_chunk,
                    )
                    if wh_dir:
                        inst_msg = "[Local Sandbox] Installing requirements offline into isolated .venv from wheelhouse...\n"
                        log_fp.write(inst_msg)
                        if on_output_chunk:
                            on_output_chunk(inst_msg)
                        st_i, rc_i, err_i = await _stream_subprocess_to_log(
                            build_id=build_id,
                            cmd_or_args=[
                                str(venv_python),
                                "-m",
                                "pip",
                                "install",
                                "--no-index",
                                "--find-links",
                                str(wh_dir),
                                "-r",
                                str(workspace_source_dir / "requirements.txt"),
                            ],
                            cwd=workspace_source_dir,
                            env=clean_env,
                            log_fp=log_fp,
                            timeout_seconds=timeout_seconds,
                            on_output_chunk=on_output_chunk,
                            use_shell=False,
                        )
                        if st_i != BuildStatus.COMPLETED or rc_i != 0:
                            status, exit_code, error_msg = st_i, rc_i, err_i
                except Exception as exc:
                    status = BuildStatus.FAILED
                    exit_code = 1
                    error_msg = str(exc)
                    log_fp.write(f"\n[EXCEPTION] {exc}\n")

            if status == BuildStatus.BUILDING:
                status, exit_code, error_msg = await _stream_subprocess_to_log(
                    build_id=build_id,
                    cmd_or_args=build_command,
                    cwd=workspace_source_dir,
                    env=clean_env,
                    log_fp=log_fp,
                    timeout_seconds=timeout_seconds,
                    on_output_chunk=on_output_chunk,
                    use_shell=True,
                )

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
            metadata={
                "command": build_command,
                "dependency_mode": dependency_mode,
                "isolation_tier": "host_sandbox_venv_isolated",
                "venv_path": str(venv_dir),
                "venv_python": str(venv_python),
            },
        )


class DockerBuildBackend(BaseBuildBackend):
    """Executes builds inside fresh isolated Docker containers without touching host .venv."""

    @staticmethod
    def _find_cached_python_image() -> Optional[str]:
        """Return a locally cached Python container image if available offline."""
        try:
            proc = subprocess.run(
                ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if proc.returncode == 0:
                for line in proc.stdout.splitlines():
                    tag = line.strip()
                    if tag and "<none>" not in tag and tag.startswith(("python:", "library/python:")):
                        return tag
        except Exception:
            pass
        return None

    @staticmethod
    def _image_exists_locally(image_tag: str) -> bool:
        try:
            proc = subprocess.run(
                ["docker", "image", "inspect", image_tag],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            return proc.returncode == 0
        except Exception:
            return False

    @classmethod
    def _select_base_image(cls, workspace_source_dir: Path) -> str:
        """Choose an appropriate Linux base container image for non-Dockerfile repositories."""
        if (workspace_source_dir / "Cargo.toml").is_file():
            return "rust:1.80-slim"
        if (workspace_source_dir / "go.mod").is_file():
            return "golang:1.22"
        if (workspace_source_dir / "package.json").is_file():
            return "node:20-slim"
        if any(
            (workspace_source_dir / f).is_file()
            for f in ("pyproject.toml", "setup.py", "requirements.txt")
        ) or any(workspace_source_dir.glob("*.py")):
            cached = cls._find_cached_python_image()
            return cached or "python:3.11-slim"
        return "alpine:3.20"

    @staticmethod
    async def _pull_container_logs_and_inspect(
        ctr_name: str,
        log_fp,
        on_output_chunk: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        """Retrieve complete container logs and inspect metadata BEFORE destroying the container."""
        inspect_meta: Dict[str, Any] = {}
        try:
            proc_logs = await asyncio.create_subprocess_exec(
                "docker",
                "logs",
                "--timestamps",
                ctr_name,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            out_bytes, _ = await asyncio.wait_for(proc_logs.communicate(), timeout=15)
            pulled_txt = out_bytes.decode("utf-8", errors="replace").strip()
            banner = f"\n=== Docker Container Logs (Pulled Prior to Removal: {ctr_name}) ===\n"
            log_fp.write(banner)
            if pulled_txt:
                log_fp.write(pulled_txt + "\n")
            else:
                log_fp.write("(container emitted no additional buffered logs)\n")
            log_fp.flush()
            if on_output_chunk and pulled_txt:
                on_output_chunk(banner + pulled_txt + "\n")
        except Exception as exc:
            log_fp.write(f"\n[Docker Logs Warning] Could not pull logs for {ctr_name}: {exc}\n")

        try:
            proc_insp = await asyncio.create_subprocess_exec(
                "docker",
                "inspect",
                ctr_name,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            insp_bytes, _ = await asyncio.wait_for(proc_insp.communicate(), timeout=10)
            if proc_insp.returncode == 0:
                parsed = json.loads(insp_bytes.decode("utf-8", errors="replace"))
                if isinstance(parsed, list) and parsed:
                    state_obj = parsed[0].get("State", {})
                    inspect_meta = {
                        "container_id": str(parsed[0].get("Id", ""))[:16],
                        "container_name": ctr_name,
                        "image": parsed[0].get("Image", ""),
                        "status": state_obj.get("Status", ""),
                        "exit_code": state_obj.get("ExitCode"),
                        "oom_killed": state_obj.get("OOMKilled", False),
                        "started_at": state_obj.get("StartedAt", ""),
                        "finished_at": state_obj.get("FinishedAt", ""),
                    }
        except Exception as exc:
            inspect_meta["inspect_error"] = str(exc)

        return inspect_meta

    @staticmethod
    async def _remove_container(ctr_name: str, log_fp) -> None:
        """Forcibly remove ephemeral container only AFTER logs & metadata have been retrieved."""
        try:
            proc_rm = await asyncio.create_subprocess_exec(
                "docker",
                "rm",
                "-f",
                ctr_name,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc_rm.wait(), timeout=15)
            log_fp.write(f"[Docker Cleanup] Removed container {ctr_name}\n")
            log_fp.flush()
        except Exception as exc:
            log_fp.write(f"[Docker Cleanup Warning] Failed removing {ctr_name}: {exc}\n")

    @classmethod
    async def _ensure_docker_wheelhouse(
        cls,
        build_id: str,
        workspace_source_dir: Path,
        base_img: str,
        log_fp,
        on_output_chunk: Optional[Callable[[str], None]] = None,
    ) -> Optional[Path]:
        """Cache Linux wheels once per requirements.txt SHA-256 so offline Build A/B never hit PyPI."""
        img_tag_safe = base_img.replace(":", "_").replace("/", "_")
        wh_dir = _compute_wheelhouse_dir(workspace_source_dir, f"linux-{img_tag_safe}")
        if not wh_dir:
            return None
        ready_marker = wh_dir / ".ready"
        if ready_marker.is_file():
            note = f"[Wheelhouse Cache] Reusing Linux wheelhouse: {wh_dir}\n"
            log_fp.write(note)
            if on_output_chunk:
                on_output_chunk(note)
            return wh_dir

        wh_dir.mkdir(parents=True, exist_ok=True)
        prep_ctr = f"reproforge-prep-{build_id}"
        msg = f"[Wheelhouse Prep] Downloading Linux wheels once inside prep container ({base_img}) into {wh_dir}...\n"
        log_fp.write(msg)
        if on_output_chunk:
            on_output_chunk(msg)

        prep_args = [
            "docker",
            "run",
            "--name",
            prep_ctr,
            "-v",
            f"{workspace_source_dir}:/src:ro",
            "-v",
            f"{wh_dir}:/wheelhouse",
            base_img,
            "pip",
            "download",
            "-r",
            "/src/requirements.txt",
            "-d",
            "/wheelhouse",
        ]
        try:
            st, rc, _ = await _stream_subprocess_to_log(
                build_id=f"{build_id}-prep",
                cmd_or_args=prep_args,
                cwd=workspace_source_dir,
                env=None,
                log_fp=log_fp,
                timeout_seconds=600,
                on_output_chunk=on_output_chunk,
                use_shell=False,
            )
        finally:
            await cls._pull_container_logs_and_inspect(prep_ctr, log_fp, on_output_chunk=None)
            await cls._remove_container(prep_ctr, log_fp)

        if st == BuildStatus.COMPLETED and rc == 0:
            ready_marker.write_text("ok\n", encoding="utf-8")
            return wh_dir

        shutil.rmtree(wh_dir, ignore_errors=True)
        raise RuntimeError(f"Docker Linux wheelhouse prep failed (status={st.value}, exit={rc})")

    async def execute(
        self,
        build_id: str,
        workspace_source_dir: Path,
        build_command: str = "docker build --no-cache",
        env_vars: Optional[Dict[str, str]] = None,
        timeout_seconds: int = 1200,
        on_output_chunk: Optional[Callable[[str], None]] = None,
        network_access: bool = False,
        memory_limit: str = "2g",
        cpu_limit: str = "2",
        dependency_mode: str = "full",
    ) -> BuildExecutionResult:
        docker_bin = shutil.which("docker")
        if not docker_bin:
            raise RuntimeError("Docker executable not found on system PATH.")

        log_file = workspace_source_dir.parent / f"{build_id}.log"
        started_at = datetime.now(timezone.utc)
        start_time = time.monotonic()

        has_dockerfile = (workspace_source_dir / "Dockerfile").is_file() or (
            workspace_source_dir / "Containerfile"
        ).is_file()
        image_tag = f"reproforge-{build_id}:target"
        ctr_name = f"reproforge-ctr-{build_id}"
        metadata: Dict[str, Any] = {
            "network_disabled": not network_access,
            "memory_limit": memory_limit,
            "cpu_limit": cpu_limit,
            "dependency_mode": dependency_mode,
        }

        status = BuildStatus.BUILDING
        exit_code = -1
        error_msg: Optional[str] = None

        with open(log_file, "w", encoding="utf-8") as log_fp:
            net_mode_str = "bridged" if network_access else "offline (--network=none)"
            header = (
                f"=== ReproForge Build Log: {build_id} ===\n"
                f"Timestamp: {started_at.isoformat()}\n"
                f"Isolation: Docker Container (Isolated) [{net_mode_str}, memory={memory_limit}, cpus={cpu_limit}]\n"
                f"Dependency Mode: {dependency_mode}\n"
                f"Working Directory: {workspace_source_dir}\n"
                f"=========================================\n\n"
            )
            log_fp.write(header)
            log_fp.flush()
            if on_output_chunk:
                on_output_chunk(header)

            if has_dockerfile:
                docker_args = [
                    "docker",
                    "build",
                    "--no-cache",
                    "--progress=plain",
                    f"--memory={memory_limit}",
                ]
                if not network_access:
                    docker_args.append("--network=none")
                docker_args.extend(["-t", image_tag, "."])

                log_fp.write(f"Command: {' '.join(docker_args)}\n\n")
                status, exit_code, error_msg = await _stream_subprocess_to_log(
                    build_id=build_id,
                    cmd_or_args=docker_args,
                    cwd=workspace_source_dir,
                    env=None,
                    log_fp=log_fp,
                    timeout_seconds=timeout_seconds,
                    on_output_chunk=on_output_chunk,
                    use_shell=False,
                )
                metadata["image_tag"] = image_tag
                metadata["isolation_tier"] = "container_dockerfile_build"
            else:
                base_img = await asyncio.to_thread(self._select_base_image, workspace_source_dir)
                metadata["base_image"] = base_img
                metadata["isolation_tier"] = "container_ephemeral"

                if not await asyncio.to_thread(self._image_exists_locally, base_img):
                    pull_msg = f"[Docker Setup] Base image '{base_img}' not cached locally; pulling once...\n"
                    log_fp.write(pull_msg)
                    if on_output_chunk:
                        on_output_chunk(pull_msg)
                    p_st, p_rc, p_err = await _stream_subprocess_to_log(
                        build_id=f"{build_id}-pull",
                        cmd_or_args=["docker", "pull", base_img],
                        cwd=workspace_source_dir,
                        env=None,
                        log_fp=log_fp,
                        timeout_seconds=600,
                        on_output_chunk=on_output_chunk,
                        use_shell=False,
                    )
                    if p_st != BuildStatus.COMPLETED or p_rc != 0:
                        status, exit_code, error_msg = p_st, p_rc, p_err or f"Failed pulling {base_img}"

                wh_dir: Optional[Path] = None
                has_req = (workspace_source_dir / "requirements.txt").is_file()
                if status == BuildStatus.BUILDING and dependency_mode == "full" and has_req:
                    try:
                        wh_dir = await self._ensure_docker_wheelhouse(
                            build_id=build_id,
                            workspace_source_dir=workspace_source_dir,
                            base_img=base_img,
                            log_fp=log_fp,
                            on_output_chunk=on_output_chunk,
                        )
                    except Exception as exc:
                        status = BuildStatus.FAILED
                        exit_code = 1
                        error_msg = str(exc)
                        log_fp.write(f"\n[EXCEPTION] {exc}\n")

                if status == BuildStatus.BUILDING:
                    inner_parts: List[str] = []
                    if wh_dir and has_req:
                        inner_parts.append(
                            "python -m venv /tmp/ctr_venv && "
                            "export PATH=\"/tmp/ctr_venv/bin:$PATH\" && "
                            "pip install --no-index --find-links=/wheelhouse -r /workspace/requirements.txt"
                        )
                    inner_cmd = build_command or "python -m compileall -x '(\\.git|\\.venv|dist)' ."
                    inner_parts.append(inner_cmd)
                    combined_sh = " && ".join(inner_parts)

                    run_args: List[str] = [
                        "docker",
                        "run",
                        "--name",
                        ctr_name,
                        f"--memory={memory_limit}",
                        f"--cpus={cpu_limit}",
                    ]
                    if not network_access:
                        run_args.append("--network=none")
                    if env_vars:
                        for k, v in env_vars.items():
                            run_args.extend(["-e", f"{k}={v}"])
                    run_args.extend(["-v", f"{workspace_source_dir}:/workspace"])
                    if wh_dir:
                        run_args.extend(["-v", f"{wh_dir}:/wheelhouse:ro"])
                    run_args.extend(["-w", "/workspace", base_img, "sh", "-c", combined_sh])

                    log_fp.write(f"Container: {ctr_name}\nImage: {base_img}\nCommand: {combined_sh}\n\n")
                    try:
                        status, exit_code, error_msg = await _stream_subprocess_to_log(
                            build_id=build_id,
                            cmd_or_args=run_args,
                            cwd=workspace_source_dir,
                            env=None,
                            log_fp=log_fp,
                            timeout_seconds=timeout_seconds,
                            on_output_chunk=on_output_chunk,
                            use_shell=False,
                        )
                    finally:
                        # Pull full container logs & inspect metadata BEFORE removing container
                        inspect_info = await self._pull_container_logs_and_inspect(
                            ctr_name, log_fp, on_output_chunk=on_output_chunk
                        )
                        metadata["container_inspect"] = inspect_info
                        await self._remove_container(ctr_name, log_fp)

        end_time = time.monotonic()
        completed_at = datetime.now(timezone.utc)
        duration = round(end_time - start_time, 3)

        logger.info(f"[{build_id}] Docker execution finished: status={status.value}, exit={exit_code}, dur={duration}s")

        return BuildExecutionResult(
            build_id=build_id,
            backend_type="docker",
            status=status,
            exit_code=exit_code,
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=duration,
            log_file_path=str(log_file),
            error_message=error_msg,
            metadata=metadata,
        )
