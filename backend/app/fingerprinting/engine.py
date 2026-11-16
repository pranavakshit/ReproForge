"""ReproForge Environment Fingerprinting Engine.

Captures structured, versioned telemetry of the host system, container runtime,
compiler toolchains, locale, and build-time environment variables. Computes
a canonical cryptographic digest representing the environmental state.
"""

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional
import psutil

from backend.app.schemas.entities import (
    EnvironmentFingerprintCreate,
    LocaleInfo,
    SystemInfo,
    ToolchainInfo,
)
from backend.app.core.logging import get_logger

logger = get_logger("reproforge.fingerprinting")


# Regex patterns identifying potential secrets in environment variables
SECRET_KEY_PATTERNS = re.compile(
    r"(?i)(key|secret|token|pass|auth|credential|cert|jwt|signature|private)"
)


class EnvironmentFingerprintEngine:
    """Engine responsible for capturing, redacting, and digesting build environments."""

    def __init__(self, whitelisted_env_prefixes: Optional[List[str]] = None) -> None:
        self.whitelisted_env_prefixes = whitelisted_env_prefixes or [
            "BUILD_",
            "CI_",
            "GIT_",
            "DOCKER_",
            "PYTHON",
            "NODE_",
            "LANG",
            "LC_",
            "TZ",
            "PATH",
            "CC",
            "CXX",
            "CFLAGS",
            "LDFLAGS",
            "SOURCE_DATE_EPOCH",
        ]

    def capture_system_info(self) -> SystemInfo:
        """Capture underlying host operating system and hardware parameters."""
        mem = psutil.virtual_memory()
        raw_hostname = platform.node()
        # Hash hostname for privacy while keeping deterministic identity
        hostname_masked = hashlib.sha256(raw_hostname.encode("utf-8")).hexdigest()[:12]

        return SystemInfo(
            os_name=platform.system(),
            os_release=platform.release(),
            os_version=platform.version(),
            kernel_release=platform.uname().release,
            cpu_arch=platform.machine(),
            cpu_count=psutil.cpu_count(logical=True) or 1,
            total_ram_bytes=mem.total,
            hostname_masked=f"node-{hostname_masked}",
        )

    def _probe_command_version(self, cmd: List[str]) -> Optional[str]:
        """Safely probe command version output with strict timeout."""
        try:
            if not shutil.which(cmd[0]):
                return None
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if result.returncode == 0:
                output = result.stdout.strip() or result.stderr.strip()
                # Return first line of version output
                return output.splitlines()[0] if output else None
        except Exception as e:
            logger.debug(f"Failed to probe {cmd[0]}: {e}")
        return None

    def capture_toolchain_info(self) -> ToolchainInfo:
        """Probe installed compiler, interpreter, and build toolchain versions."""
        tools: Dict[str, Dict[str, Any]] = {}

        # 1. Python runtime
        tools["python"] = {
            "version": platform.python_version(),
            "compiler": platform.python_compiler(),
            "implementation": platform.python_implementation(),
            "executable": sys.executable,
        }

        # 2. Git
        git_ver = self._probe_command_version(["git", "--version"])
        if git_ver:
            tools["git"] = {"version": git_ver}

        # 3. Docker CLI
        docker_ver = self._probe_command_version(["docker", "--version"])
        if docker_ver:
            tools["docker_cli"] = {"version": docker_ver}

        # 4. GCC / Clang
        gcc_ver = self._probe_command_version(["gcc", "--version"])
        if gcc_ver:
            tools["gcc"] = {"version": gcc_ver}
        else:
            clang_ver = self._probe_command_version(["clang", "--version"])
            if clang_ver:
                tools["clang"] = {"version": clang_ver}

        # 5. Node.js
        node_ver = self._probe_command_version(["node", "--version"])
        if node_ver:
            tools["node"] = {"version": node_ver}

        # 6. Make
        make_ver = self._probe_command_version(["make", "--version"])
        if make_ver:
            tools["make"] = {"version": make_ver}

        return ToolchainInfo(tools=tools)

    def capture_locale_info(self) -> LocaleInfo:
        """Capture timezone, locale variables, and file creation mask."""
        tz_name = time.tzname[0] if time.tzname else "UTC"

        # Safe umask detection
        try:
            current_umask = os.umask(0o022)
            os.umask(current_umask)  # restore original umask
            umask_str = oct(current_umask)
        except (AttributeError, OSError):
            umask_str = "unknown"

        return LocaleInfo(
            lang=os.environ.get("LANG"),
            lc_all=os.environ.get("LC_ALL"),
            timezone=tz_name,
            umask=umask_str,
        )

    def capture_environment_variables(self) -> Dict[str, str]:
        """Capture and redact environment variables to prevent secret leakage."""
        sanitized: Dict[str, str] = {}

        for key, value in sorted(os.environ.items()):
            # Check if key matches secret patterns
            if SECRET_KEY_PATTERNS.search(key):
                sanitized[key] = "[REDACTED_SECRET]"
                continue

            # Check if variable belongs to build-relevant prefixes or whitelist
            is_relevant = any(
                key.startswith(prefix) for prefix in self.whitelisted_env_prefixes
            )
            if is_relevant:
                sanitized[key] = value

        return sanitized

    def capture_container_runtime_info(self) -> Dict[str, Any]:
        """Capture Docker Engine and BuildKit daemon info if available."""
        runtime_info: Dict[str, Any] = {"available": False}
        try:
            import docker
            client = docker.from_env(timeout=3)
            ping_ok = client.ping()
            if ping_ok:
                version_data = client.version()
                runtime_info = {
                    "available": True,
                    "engine_version": version_data.get("Version"),
                    "api_version": version_data.get("ApiVersion"),
                    "os": version_data.get("Os"),
                    "arch": version_data.get("Arch"),
                    "kernel_version": version_data.get("KernelVersion"),
                    "buildkit_supported": True,
                }
        except Exception as e:
            logger.debug(f"Docker daemon not directly reachable: {e}")
            runtime_info["error"] = str(e)

        return runtime_info

    def compute_canonical_hash(
        self,
        system_info: SystemInfo,
        toolchain_info: ToolchainInfo,
        locale_info: LocaleInfo,
        env_vars: Dict[str, str],
        container_info: Dict[str, Any],
    ) -> str:
        """Compute canonical SHA-256 digest over sorted JSON representation."""
        payload = {
            "system": system_info.model_dump(),
            "toolchains": toolchain_info.model_dump(),
            "locale": locale_info.model_dump(),
            "environment_variables": env_vars,
            "container_runtime": container_info,
        }
        canonical_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

    def capture_fingerprint(self) -> EnvironmentFingerprintCreate:
        """Execute full environment capture and return structured fingerprint entity."""
        logger.info("Executing environment fingerprint capture...")
        system_info = self.capture_system_info()
        toolchain_info = self.capture_toolchain_info()
        locale_info = self.capture_locale_info()
        env_vars = self.capture_environment_variables()
        container_info = self.capture_container_runtime_info()

        fingerprint_hash = self.compute_canonical_hash(
            system_info, toolchain_info, locale_info, env_vars, container_info
        )

        logger.info(f"Fingerprint captured. Canonical Digest: {fingerprint_hash[:16]}...")
        return EnvironmentFingerprintCreate(
            fingerprint_hash=fingerprint_hash,
            system_info=system_info,
            toolchain_info=toolchain_info,
            locale_info=locale_info,
            environment_variables=env_vars,
            container_runtime_info=container_info,
        )


# Global instance
fingerprinter = EnvironmentFingerprintEngine()
