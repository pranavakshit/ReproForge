"""Unit tests for ReproForge CLI."""

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
