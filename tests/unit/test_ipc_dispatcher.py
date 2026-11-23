"""Unit tests for the IPC RequestDispatcher routing to services."""

import asyncio
from unittest.mock import AsyncMock, MagicMock
import pytest
from backend.app.ipc.dispatcher import RequestDispatcher
from backend.app.ipc.protocol import JsonRpcErrorCode, JsonRpcRequest


@pytest.mark.asyncio
async def test_dispatcher_system_handshake():
    dispatcher = RequestDispatcher()
    req = JsonRpcRequest(id=1, method="system.handshake")
    resp = await dispatcher.dispatch(req)

    assert resp.id == 1
    assert resp.result["protocol_version"] == "1.0.0"
    assert "capabilities" in resp.result
    assert resp.result["sqlite_wal_active"] is True
    assert resp.result["engine_pid"] > 0


@pytest.mark.asyncio
async def test_dispatcher_system_ping():
    dispatcher = RequestDispatcher()
    req = JsonRpcRequest(id="ping-1", method="system.ping", params={"timestamp": 12345.0})
    resp = await dispatcher.dispatch(req)

    assert resp.id == "ping-1"
    assert resp.result["status"] == "healthy"
    assert resp.result["timestamp"] == 12345.0


@pytest.mark.asyncio
async def test_dispatcher_system_shutdown():
    dispatcher = RequestDispatcher()
    assert not dispatcher.shutdown_requested.is_set()

    req = JsonRpcRequest(id=99, method="system.shutdown")
    resp = await dispatcher.dispatch(req)

    assert resp.id == 99
    assert resp.result["acknowledged"] is True
    assert dispatcher.shutdown_requested.is_set()


@pytest.mark.asyncio
async def test_dispatcher_method_not_found():
    dispatcher = RequestDispatcher()
    req = JsonRpcRequest(id=5, method="unknown.action")
    resp = await dispatcher.dispatch(req)

    assert resp.id == 5
    assert resp.error.code == JsonRpcErrorCode.METHOD_NOT_FOUND
    assert "unknown.action" in resp.error.message


@pytest.mark.asyncio
async def test_dispatcher_notification_returns_none():
    dispatcher = RequestDispatcher()
    # Notification has id=None
    req = JsonRpcRequest(id=None, method="system.ping", params={"timestamp": 99.0})
    resp = await dispatcher.dispatch(req)
    assert resp is None


@pytest.mark.asyncio
async def test_dispatcher_notification_unknown_method_returns_none():
    dispatcher = RequestDispatcher()
    req = JsonRpcRequest(id=None, method="unknown.noise")
    resp = await dispatcher.dispatch(req)
    assert resp is None


@pytest.mark.asyncio
async def test_dispatcher_build_cancel_invalid_params():
    dispatcher = RequestDispatcher()
    # Missing required 'build_id'
    req = JsonRpcRequest(id=10, method="build.cancel", params={})
    resp = await dispatcher.dispatch(req)

    assert resp.id == 10
    assert resp.error.code == JsonRpcErrorCode.INVALID_PARAMS
    assert "build_id" in resp.error.message


@pytest.mark.asyncio
async def test_dispatcher_service_routing_mocked():
    mock_fp_svc = MagicMock()
    mock_fp = MagicMock()
    mock_fp.model_dump.return_value = {"fingerprint_id": "fp-123", "sha256": "abcdef"}
    mock_fp_svc.capture_and_persist = AsyncMock(return_value=mock_fp)

    dispatcher = RequestDispatcher(fingerprint_service=mock_fp_svc)
    req = JsonRpcRequest(id="fp-call", method="fingerprint.capture", params={"repo_id": "r-1"})
    resp = await dispatcher.dispatch(req)

    assert resp.id == "fp-call"
    assert resp.result["fingerprint_id"] == "fp-123"
    mock_fp_svc.capture_and_persist.assert_awaited_once_with(repo_id="r-1")


@pytest.mark.asyncio
async def test_dispatcher_overview_metrics_routing():
    mock_overview_svc = MagicMock()
    mock_overview_svc.get_overview_metrics = AsyncMock(return_value={
        "total_builds": 42,
        "bit_for_bit_rate": "88.0%",
        "normalized_rate": "95.0%",
        "unresolved_count": 2,
    })

    dispatcher = RequestDispatcher(overview_service=mock_overview_svc)
    req = JsonRpcRequest(id="ov-1", method="overview.get_metrics")
    resp = await dispatcher.dispatch(req)

    assert resp.id == "ov-1"
    assert resp.result["total_builds"] == 42
    assert resp.result["bit_for_bit_rate"] == "88.0%"


@pytest.mark.asyncio
async def test_dispatcher_repository_register_and_revisions_mocked():
    mock_repo_svc = MagicMock()
    mock_repo = MagicMock()
    mock_repo.id = "repo-xyz"
    mock_repo.name = "ReproForge"
    mock_repo.model_dump.return_value = {"id": "repo-xyz", "name": "ReproForge"}
    mock_repo_svc.register_repository = AsyncMock(return_value=mock_repo)

    mock_rev = MagicMock()
    mock_rev.commit_sha = "abc12345"
    mock_rev.model_dump.return_value = {"commit_sha": "abc12345", "author_name": "Dev"}
    mock_repo_svc.get_revisions = AsyncMock(return_value=[mock_rev])

    dispatcher = RequestDispatcher(repository_service=mock_repo_svc)

    # Register
    reg_req = JsonRpcRequest(id=101, method="repository.register", params={"name": "ReproForge", "repo_url": "."})
    reg_resp = await dispatcher.dispatch(reg_req)
    assert reg_resp.id == 101
    assert reg_resp.result["name"] == "ReproForge"

    # Get Revisions
    rev_req = JsonRpcRequest(id=102, method="repository.get_revisions", params={"repository_id": "repo-xyz"})
    rev_resp = await dispatcher.dispatch(rev_req)
    assert rev_resp.id == 102
    assert rev_resp.result["total_count"] == 1
    assert rev_resp.result["revisions"][0]["commit_sha"] == "abc12345"

