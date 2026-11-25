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

        # 4. Check Rust Cargo builds
        rust_sys = self._detect_rust(repo_path)
        if rust_sys:
            detected.append(rust_sys)

        # 5. Check Go module builds
        go_sys = self._detect_go(repo_path)
        if go_sys:
            detected.append(go_sys)

        # 6. Check Makefile
        make_sys = self._detect_make(repo_path)
        if make_sys:
            detected.append(make_sys)

        # 7. Check GitHub Actions Workflows (.github/workflows/*.yml)
        gha_sys = self._detect_github_actions(repo_path)
        if gha_sys:
            detected.append(gha_sys)

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

    def audit_reproducibility_readiness(self, repo_path: Path) -> Dict[str, Any]:
        """Run non-destructive static checks for lockfiles, mutable tags, CI pin hygiene, and epoch support."""
        systems = self.detect(repo_path)
        non_generic = [s for s in systems if s.system_type != "generic"]

        mutable_docker_tags: List[str] = []
        unpinned_actions: List[str] = []
        has_any_lockfile = False
        has_source_date_epoch = False

        for sys_info in non_generic:
            if sys_info.has_lockfile:
                has_any_lockfile = True
            if sys_info.system_type == "docker":
                for img in sys_info.base_images:
                    if "@sha256:" not in img and not img.lower().startswith("scratch"):
                        mutable_docker_tags.append(img)
            if sys_info.system_type == "github_actions":
                unpinned_actions.extend(sys_info.metadata.get("unpinned_actions", []))
                if sys_info.metadata.get("has_source_date_epoch"):
                    has_source_date_epoch = True

        # Check top-level build scripts/configs for SOURCE_DATE_EPOCH references
        for fname in ["Dockerfile", "Makefile", "pyproject.toml", "build.sh", "Cargo.toml"]:
            fpath = repo_path / fname
            if fpath.is_file():
                try:
                    if "SOURCE_DATE_EPOCH" in fpath.read_text(encoding="utf-8", errors="ignore"):
                        has_source_date_epoch = True
                        break
                except Exception:
                    pass

        warnings: List[str] = []
        if non_generic and not has_any_lockfile:
            warnings.append("Missing pinned dependency lockfile")
        if mutable_docker_tags:
            warnings.append(f"Mutable base image tag(s): {', '.join(mutable_docker_tags[:3])}")
        if unpinned_actions:
            warnings.append(f"Unpinned GitHub Actions ({len(unpinned_actions)} uses tags vs SHA)")
        if not has_source_date_epoch:
            warnings.append("No explicit SOURCE_DATE_EPOCH timestamp pin found")

        return {
            "detected_systems": [s.system_type for s in non_generic] or ["generic"],
            "primary_build_system": systems[0].system_type if systems else "generic",
            "has_lockfile": has_any_lockfile,
            "mutable_docker_tags": mutable_docker_tags,
            "unpinned_github_actions": unpinned_actions[:15],
            "has_source_date_epoch": has_source_date_epoch,
            "readiness_warnings": warnings,
            "readiness_status": "hardened" if not warnings else "warnings_detected",
        }

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

    def _detect_rust(self, repo_path: Path) -> Optional[DetectedBuildSystem]:
        """Inspect Rust Cargo workspace or desktop manifest."""
        candidates = ["Cargo.toml", "desktop/Cargo.toml"]
        cargo_path = next((repo_path / c for c in candidates if (repo_path / c).is_file()), None)
        if not cargo_path:
            return None

        lock_candidates = [cargo_path.parent / "Cargo.lock", repo_path / "Cargo.lock"]
        lock_file = next((p for p in lock_candidates if p.is_file()), None)
        rel_primary = str(cargo_path.relative_to(repo_path)).replace("\\", "/")
        rel_lock = str(lock_file.relative_to(repo_path)).replace("\\", "/") if lock_file else None

        return DetectedBuildSystem(
            system_type="rust",
            primary_config_file=rel_primary,
            build_files_found=[rel_primary] + ([rel_lock] if rel_lock else []),
            has_lockfile=lock_file is not None,
            lockfile_path=rel_lock,
            suggested_build_command=f"cargo build --release --manifest-path {rel_primary} --locked" if lock_file else f"cargo build --release --manifest-path {rel_primary}",
            metadata={"locked_flag": lock_file is not None},
        )

    def _detect_go(self, repo_path: Path) -> Optional[DetectedBuildSystem]:
        """Inspect Go module files."""
        go_mod = repo_path / "go.mod"
        if not go_mod.is_file():
            return None
        has_sum = (repo_path / "go.sum").is_file()
        return DetectedBuildSystem(
            system_type="go",
            primary_config_file="go.mod",
            build_files_found=["go.mod"] + (["go.sum"] if has_sum else []),
            has_lockfile=has_sum,
            lockfile_path="go.sum" if has_sum else None,
            suggested_build_command="go build -trimpath ./...",
            metadata={"go_sum_present": has_sum},
        )

    def _detect_github_actions(self, repo_path: Path) -> Optional[DetectedBuildSystem]:
        """Inspect GitHub Actions workflow files for CI/CD reproducibility hygiene."""
        wf_dir = repo_path / ".github" / "workflows"
        if not wf_dir.is_dir():
            return None

        wf_files = sorted(
            [p for p in wf_dir.iterdir() if p.is_file() and p.suffix.lower() in (".yml", ".yaml")],
            key=lambda p: p.name.lower(),
        )
        if not wf_files:
            return None

        rel_files = [str(p.relative_to(repo_path)).replace("\\", "/") for p in wf_files]
        unpinned_actions: List[str] = []
        has_sde = False

        sha_pin_pattern = re.compile(r"@[0-9a-f]{40}\b", re.IGNORECASE)
        uses_pattern = re.compile(r"uses:\s*([^\s#]+)", re.IGNORECASE)

        for wf in wf_files:
            text = wf.read_text(encoding="utf-8", errors="ignore")
            if "SOURCE_DATE_EPOCH" in text:
                has_sde = True
            for ref in uses_pattern.findall(text):
                if ref.startswith("./") or sha_pin_pattern.search(ref):
                    continue
                unpinned_actions.append(f"{wf.name}: {ref}")

        return DetectedBuildSystem(
            system_type="github_actions",
            primary_config_file=rel_files[0],
            build_files_found=rel_files,
            has_lockfile=len(unpinned_actions) == 0,
            suggested_build_command="",
            metadata={
                "workflow_count": len(wf_files),
                "unpinned_actions": unpinned_actions,
                "has_source_date_epoch": has_sde,
            },
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
