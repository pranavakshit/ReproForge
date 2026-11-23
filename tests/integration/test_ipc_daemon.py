"""Integration test verifying end-to-end Python engine daemon JSON-RPC IPC over stdio."""

import json
import subprocess
import sys
import time
import pytest


def test_daemon_stdio_ipc_lifecycle():
    """Verify stdio JSON-RPC IPC lifecycle from spawn to clean shutdown."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "backend.app.cli.main", "daemon", "--no-log-file"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    try:
        # 1. Send system.handshake
        handshake_req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "system.handshake"}) + "\n"
        proc.stdin.write(handshake_req)
        proc.stdin.flush()

        resp_line = proc.stdout.readline()
        assert resp_line, "Daemon must return handshake response"
        handshake_resp = json.loads(resp_line)
        assert handshake_resp["id"] == 1
        assert handshake_resp["result"]["protocol_version"] == "1.0.0"
        assert handshake_resp["result"]["engine_pid"] > 0
        assert "capabilities" in handshake_resp["result"]

        # 2. Send system.ping
        ping_req = json.dumps({"jsonrpc": "2.0", "id": "p-1", "method": "system.ping", "params": {"timestamp": 100.0}}) + "\n"
        proc.stdin.write(ping_req)
        proc.stdin.flush()

        resp_line = proc.stdout.readline()
        assert resp_line, "Daemon must return ping response"
        ping_resp = json.loads(resp_line)
        assert ping_resp["id"] == "p-1"
        assert ping_resp["result"]["status"] == "healthy"

        # 3. Send overview.get_metrics
        ov_req = json.dumps({"jsonrpc": "2.0", "id": "ov-1", "method": "overview.get_metrics"}) + "\n"
        proc.stdin.write(ov_req)
        proc.stdin.flush()

        resp_line = proc.stdout.readline()
        assert resp_line, "Daemon must return overview response"
        ov_resp = json.loads(resp_line)
        assert ov_resp["id"] == "ov-1"
        assert "bit_for_bit_rate" in ov_resp["result"]
        assert "telemetry" in ov_resp["result"]

        # 4. Send repository.register
        reg_req = json.dumps({
            "jsonrpc": "2.0",
            "id": 4,
            "method": "repository.register",
            "params": {"name": "ReproForge Local", "repo_url": ".", "project_name": "Capstone"}
        }) + "\n"
        proc.stdin.write(reg_req)
        proc.stdin.flush()

        resp_line = proc.stdout.readline()
        assert resp_line, "Daemon must return repository register response"
        reg_resp = json.loads(resp_line)
        assert reg_resp["id"] == 4
        repo_id = reg_resp["result"]["id"]
        assert repo_id is not None

        # 5. Send repository.get_revisions
        rev_req = json.dumps({
            "jsonrpc": "2.0",
            "id": 5,
            "method": "repository.get_revisions",
            "params": {"repository_id": repo_id, "limit": 5}
        }) + "\n"
        proc.stdin.write(rev_req)
        proc.stdin.flush()

        resp_line = proc.stdout.readline()
        assert resp_line, "Daemon must return revisions response"
        rev_resp = json.loads(resp_line)
        assert rev_resp["id"] == 5
        assert "revisions" in rev_resp["result"]

        # 6. Send repository.list
        repo_req = json.dumps({"jsonrpc": "2.0", "id": 6, "method": "repository.list", "params": {"limit": 10}}) + "\n"
        proc.stdin.write(repo_req)
        proc.stdin.flush()

        resp_line = proc.stdout.readline()
        assert resp_line, "Daemon must return repository list response"
        repo_resp = json.loads(resp_line)
        assert repo_resp["id"] == 6
        assert "repositories" in repo_resp["result"]

        # 7. Send build.cancel for unknown ID (verifies error response and process survival)
        cancel_req = json.dumps({"jsonrpc": "2.0", "id": 7, "method": "build.cancel", "params": {"build_id": "non-existent"}}) + "\n"
        proc.stdin.write(cancel_req)
        proc.stdin.flush()

        resp_line = proc.stdout.readline()
        assert resp_line, "Daemon must return error response"
        cancel_resp = json.loads(resp_line)
        assert cancel_resp["id"] == 7
        assert "error" in cancel_resp
        assert proc.poll() is None, "Daemon process must remain alive after error response"

        # 8. Send system.shutdown
        shutdown_req = json.dumps({"jsonrpc": "2.0", "id": 8, "method": "system.shutdown"}) + "\n"
        proc.stdin.write(shutdown_req)
        proc.stdin.flush()

        resp_line = proc.stdout.readline()
        assert resp_line, "Daemon must return shutdown response"
        shutdown_resp = json.loads(resp_line)
        assert shutdown_resp["id"] == 8
        assert shutdown_resp["result"]["acknowledged"] is True

        # Wait for daemon process exit
        exit_code = proc.wait(timeout=5)
        assert exit_code == 0, f"Daemon process should exit with 0, got {exit_code}"

    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
