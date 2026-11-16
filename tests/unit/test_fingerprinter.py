"""Unit tests for the ReproForge Environment Fingerprinting Engine."""

import os
import pytest
from backend.app.fingerprinting.engine import (
    EnvironmentFingerprintEngine,
    fingerprinter,
)
from backend.app.schemas.entities import (
    LocaleInfo,
    SystemInfo,
    ToolchainInfo,
)


def test_capture_system_info():
    """Verify system info captures essential hardware and OS telemetry."""
    sys_info = fingerprinter.capture_system_info()
    assert sys_info.os_name != ""
    assert sys_info.cpu_arch != ""
    assert sys_info.cpu_count >= 1
    assert sys_info.total_ram_bytes > 0
    assert sys_info.hostname_masked.startswith("node-")
    assert len(sys_info.hostname_masked) > 5


def test_capture_toolchain_info():
    """Verify Python and standard development tools are inspected."""
    tool_info = fingerprinter.capture_toolchain_info()
    assert "python" in tool_info.tools
    assert "version" in tool_info.tools["python"]
    assert tool_info.tools["python"]["version"] != ""


def test_environment_variable_redaction():
    """Verify sensitive environment variables are masked."""
    engine = EnvironmentFingerprintEngine(whitelisted_env_prefixes=["TEST_"])

    os.environ["TEST_PUBLIC_VAR"] = "public_value_123"
    os.environ["TEST_SECRET_API_KEY"] = "super_secret_token_abc"
    os.environ["TEST_PASSWORD_DB"] = "my_db_password"

    try:
        captured = engine.capture_environment_variables()
        assert captured.get("TEST_PUBLIC_VAR") == "public_value_123"
        assert captured.get("TEST_SECRET_API_KEY") == "[REDACTED_SECRET]"
        assert captured.get("TEST_PASSWORD_DB") == "[REDACTED_SECRET]"
    finally:
        os.environ.pop("TEST_PUBLIC_VAR", None)
        os.environ.pop("TEST_SECRET_API_KEY", None)
        os.environ.pop("TEST_PASSWORD_DB", None)


def test_canonical_hash_determinism():
    """Verify identical environment inputs produce identical cryptographic digests."""
    sys_info = SystemInfo(
        os_name="Linux",
        os_release="6.1.0",
        os_version="Debian 12",
        kernel_release="6.1.0-21-amd64",
        cpu_arch="x86_64",
        cpu_count=8,
        total_ram_bytes=16000000000,
        hostname_masked="node-abcd1234ef",
    )
    tools = ToolchainInfo(
        tools={"python": {"version": "3.12.3"}, "gcc": {"version": "12.2.0"}}
    )
    locale = LocaleInfo(
        lang="C.UTF-8",
        lc_all="C.UTF-8",
        timezone="UTC",
        umask="0022",
    )
    env_vars = {"PATH": "/usr/local/bin:/usr/bin", "LANG": "C.UTF-8"}
    container_info = {"engine_version": "26.0.0", "buildkit_supported": True}

    hash1 = fingerprinter.compute_canonical_hash(
        sys_info, tools, locale, env_vars, container_info
    )
    hash2 = fingerprinter.compute_canonical_hash(
        sys_info, tools, locale, env_vars, container_info
    )

    assert hash1 == hash2
    assert len(hash1) == 64

    # Divergence test
    env_vars_diverged = {"PATH": "/usr/local/bin:/usr/bin", "LANG": "en_US.UTF-8"}
    hash_diverged = fingerprinter.compute_canonical_hash(
        sys_info, tools, locale, env_vars_diverged, container_info
    )
    assert hash1 != hash_diverged


def test_full_fingerprint_generation():
    """Verify end-to-end fingerprint capture produces a valid schema object."""
    fp = fingerprinter.capture_fingerprint()
    assert fp.fingerprint_hash is not None
    assert len(fp.fingerprint_hash) == 64
    assert fp.system_info.os_name != ""
    assert isinstance(fp.environment_variables, dict)
