"""Unit tests for ReproForge CLI."""

import io
import subprocess
import tarfile
import tempfile
from pathlib import Path
from click.testing import CliRunner
from backend.app.cli.main import cli


def test_cli_info():
    """Verify reproforge info command outputs system telemetry."""
    runner = CliRunner()
    result = runner.invoke(cli, ["info"])
    assert result.exit_code == 0
    assert "ReproForge" in result.output
    assert "System Telemetry" in result.output


def test_cli_fingerprint():
    """Verify reproforge fingerprint outputs canonical SHA-256 digest."""
    runner = CliRunner()
    result = runner.invoke(cli, ["fingerprint"])
    assert result.exit_code == 0
    assert "Canonical Environment Digest" in result.output


def test_cli_fingerprint_json():
    """Verify reproforge fingerprint --json-out outputs valid JSON."""
    runner = CliRunner()
    result = runner.invoke(cli, ["fingerprint", "--json-out"])
    assert result.exit_code == 0
    assert '"fingerprint_hash":' in result.output


def test_cli_detect():
    """Verify reproforge detect identifies build system in repository root."""
    runner = CliRunner()
    repo_root = Path(__file__).resolve().parent.parent.parent
    result = runner.invoke(cli, ["detect", str(repo_root)])
    assert result.exit_code == 0
    assert "Detected Build Systems" in result.output
    assert "python" in result.output


def test_cli_compare():
    """Verify reproforge compare executes differential analysis, diagnostics, and displays verdicts."""
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as td:
        p1 = Path(td) / "a.tar"
        p2 = Path(td) / "b.tar"

        data = b"deterministic byte payload"
        with tarfile.open(p1, "w") as t:
            ti = tarfile.TarInfo("test.txt")
            ti.size = len(data)
            ti.mtime = 1000000000
            t.addfile(ti, io.BytesIO(data))

        with tarfile.open(p2, "w") as t:
            ti = tarfile.TarInfo("test.txt")
            ti.size = len(data)
            ti.mtime = 1000005000
            t.addfile(ti, io.BytesIO(data))

        result = runner.invoke(cli, ["compare", str(p1), str(p2)])
        assert result.exit_code == 0
        assert "ReproForge Artifact Differential Engine" in result.output
        assert "Primary Reproducibility Verdict" in result.output
        assert "NORMALIZED_REPRODUCIBLE" in result.output
        assert "Mode A (Strict Bit-for-Bit):" in result.output
        assert "Mode B (Normalized Equivalence):" in result.output
        assert "Diagnostic Root-Cause Findings" in result.output
        assert "Actionable Remediation Guidance" in result.output


def test_cli_build():
    """Verify reproforge build executes an isolated build using local sandbox."""
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as td:
        repo_dir = Path(td) / "cli_test_repo"
        repo_dir.mkdir()

        subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo_dir, check=True)
        subprocess.run(["git", "config", "user.email", "test@reproforge.org"], cwd=repo_dir, check=True)

        pyproject = repo_dir / "pyproject.toml"
        pyproject.write_text('[project]\nname = "cli-test"\nversion = "0.1.0"\n', encoding="utf-8")

        subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
        subprocess.run(["git", "commit", "-m", "CLI build test commit"], cwd=repo_dir, check=True)

        result = runner.invoke(
            cli,
            [
                "build",
                "--repo",
                str(repo_dir),
                "--backend",
                "local",
                "--command",
                "python -c \"print('CLI build succeeded')\"",
            ],
        )
        assert result.exit_code == 0
        assert "ReproForge Build Orchestrator" in result.output
        assert "Build Succeeded" in result.output
