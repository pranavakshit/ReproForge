"""Unit tests for build configuration detection."""

from pathlib import Path
import pytest
from backend.app.orchestration.detector import BuildConfigurationDetector


def test_detect_docker_build(tmp_path: Path):
    """Verify Docker build detection with base images."""
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "FROM python:3.12-slim AS builder\nRUN echo building\nFROM alpine:3.19\nCOPY --from=builder /app /app",
        encoding="utf-8",
    )

    detector = BuildConfigurationDetector()
    results = detector.detect(tmp_path)

    docker_sys = next((s for s in results if s.system_type == "docker"), None)
    assert docker_sys is not None
    assert docker_sys.primary_config_file == "Dockerfile"
    assert "python:3.12-slim" in docker_sys.base_images
    assert "alpine:3.19" in docker_sys.base_images


def test_detect_python_build(tmp_path: Path):
    """Verify Python build detection with dependencies."""
    req_file = tmp_path / "requirements.txt"
    req_file.write_text("fastapi>=0.110.0\nuvicorn\n# comment\npydantic\n", encoding="utf-8")

    detector = BuildConfigurationDetector()
    results = detector.detect(tmp_path)

    py_sys = next((s for s in results if s.system_type == "python"), None)
    assert py_sys is not None
    assert py_sys.declared_dependencies_count == 3


def test_detect_node_build(tmp_path: Path):
    """Verify Node.js build detection and lockfile presence."""
    pkg_json = tmp_path / "package.json"
    pkg_json.write_text(
        '{"name": "test-app", "dependencies": {"react": "^18.0.0"}, "devDependencies": {"vite": "^5.0.0"}}',
        encoding="utf-8",
    )
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")

    detector = BuildConfigurationDetector()
    results = detector.detect(tmp_path)

    node_sys = next((s for s in results if s.system_type == "node"), None)
    assert node_sys is not None
    assert node_sys.has_lockfile is True
    assert node_sys.lockfile_path == "package-lock.json"
    assert node_sys.declared_dependencies_count == 2
