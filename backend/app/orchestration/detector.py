"""Build configuration detection and dependency inspection."""

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from backend.app.core.logging import get_logger

logger = get_logger("reproforge.orchestration.detector")


class DetectedBuildSystem(BaseModel):
    """Structured description of an identified build system."""

    system_type: str  # docker, python, node, make, generic
    primary_config_file: str
    build_files_found: List[str]
    has_lockfile: bool
    lockfile_path: Optional[str] = None
    declared_dependencies_count: int = 0
    base_images: List[str] = Field(default_factory=list)
    suggested_build_command: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class BuildConfigurationDetector:
    """Inspects repository trees to identify build instructions, toolchains, and lockfiles."""

    def detect(self, repo_path: Path) -> List[DetectedBuildSystem]:
        """Detect all applicable build systems in the repository."""
        detected: List[DetectedBuildSystem] = []

        # 1. Check Docker / Containerfile
        docker_sys = self._detect_docker(repo_path)
        if docker_sys:
            detected.append(docker_sys)

        # 2. Check Python builds
        python_sys = self._detect_python(repo_path)
        if python_sys:
            detected.append(python_sys)

        # 3. Check Node.js builds
        node_sys = self._detect_node(repo_path)
        if node_sys:
            detected.append(node_sys)

        # 4. Check Makefile
        make_sys = self._detect_make(repo_path)
        if make_sys:
            detected.append(make_sys)

        if not detected:
            logger.warning(f"No standard build system detected in {repo_path}")
            detected.append(
                DetectedBuildSystem(
                    system_type="generic",
                    primary_config_file="none",
                    build_files_found=[],
                    has_lockfile=False,
                    suggested_build_command="echo 'No build command detected'",
                )
            )

        return detected

    def _detect_docker(self, repo_path: Path) -> Optional[DetectedBuildSystem]:
        """Inspect Dockerfile or Containerfile."""
        candidates = ["Dockerfile", "Containerfile", "docker-compose.yml", "compose.yaml"]
        found = [c for c in candidates if (repo_path / c).exists()]
        if not found:
            return None

        primary = "Dockerfile" if "Dockerfile" in found else found[0]
        base_images: List[str] = []

        # Parse base images from Dockerfile
        dockerfile_path = repo_path / primary
        if dockerfile_path.exists():
            try:
                content = dockerfile_path.read_text(encoding="utf-8", errors="ignore")
                # Find all FROM statements (e.g. FROM python:3.12-slim as builder)
                matches = re.findall(r"^\s*FROM\s+([^\s]+)", content, re.MULTILINE | re.IGNORECASE)
                base_images = [m.strip() for m in matches if not m.startswith("--")]
            except Exception as e:
                logger.debug(f"Failed to parse {dockerfile_path}: {e}")

        return DetectedBuildSystem(
            system_type="docker",
            primary_config_file=primary,
            build_files_found=found,
            has_lockfile=False,  # Docker builds rely on base image digest or lockfiles inside context
            base_images=base_images,
            suggested_build_command=f"docker build -f {primary} -t reproforge-target:latest .",
            metadata={"base_images_count": len(base_images)},
        )

    def _detect_python(self, repo_path: Path) -> Optional[DetectedBuildSystem]:
        """Inspect Python build configurations."""
        candidates = ["pyproject.toml", "setup.py", "requirements.txt", "Pipfile", "poetry.lock"]
        found = [c for c in candidates if (repo_path / c).exists()]
        if not found:
            return None

        has_lock = any(lock in found for lock in ["poetry.lock", "Pipfile.lock"])
        lockfile = "poetry.lock" if "poetry.lock" in found else None

        primary = "pyproject.toml" if "pyproject.toml" in found else found[0]
        dep_count = 0

        # Count requirements in requirements.txt if present
        req_path = repo_path / "requirements.txt"
        if req_path.exists():
            try:
                lines = [
                    l.strip()
                    for l in req_path.read_text(encoding="utf-8").splitlines()
                    if l.strip() and not l.startswith("#")
                ]
                dep_count = len(lines)
            except Exception:
                pass

        cmd = "python -m build" if "pyproject.toml" in found or "setup.py" in found else "pip wheel -r requirements.txt -w dist/"

        return DetectedBuildSystem(
            system_type="python",
            primary_config_file=primary,
            build_files_found=found,
            has_lockfile=has_lock,
            lockfile_path=lockfile,
            declared_dependencies_count=dep_count,
            suggested_build_command=cmd,
            metadata={"has_requirements_txt": "requirements.txt" in found},
        )

    def _detect_node(self, repo_path: Path) -> Optional[DetectedBuildSystem]:
        """Inspect Node.js package configurations."""
        pkg_json = repo_path / "package.json"
        if not pkg_json.exists():
            return None

        found = ["package.json"]
        lockfile = None
        has_lock = False

        for lock in ["package-lock.json", "yarn.lock", "pnpm-lock.yaml"]:
            if (repo_path / lock).exists():
                found.append(lock)
                has_lock = True
                lockfile = lock
                break

        dep_count = 0
        try:
            data = json.loads(pkg_json.read_text(encoding="utf-8"))
            deps = data.get("dependencies", {})
            dev_deps = data.get("devDependencies", {})
            dep_count = len(deps) + len(dev_deps)
        except Exception:
            pass

        return DetectedBuildSystem(
            system_type="node",
            primary_config_file="package.json",
            build_files_found=found,
            has_lockfile=has_lock,
            lockfile_path=lockfile,
            declared_dependencies_count=dep_count,
            suggested_build_command="npm run build" if has_lock else "npm install && npm run build",
            metadata={"lockfile_type": lockfile},
        )

    def _detect_make(self, repo_path: Path) -> Optional[DetectedBuildSystem]:
        """Inspect Makefile."""
        if not (repo_path / "Makefile").exists():
            return None
        return DetectedBuildSystem(
            system_type="make",
            primary_config_file="Makefile",
            build_files_found=["Makefile"],
            has_lockfile=False,
            suggested_build_command="make",
            metadata={},
        )
