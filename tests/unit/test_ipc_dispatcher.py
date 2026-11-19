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
