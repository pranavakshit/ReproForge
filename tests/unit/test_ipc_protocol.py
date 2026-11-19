"""Unit tests for JSON-RPC 2.0 protocol schema framing and validation."""

import pytest
from backend.app.ipc.protocol import (
    JsonRpcErrorCode,
    JsonRpcErrorObject,
    JsonRpcErrorResponse,
    JsonRpcNotification,
    JsonRpcRequest,
    JsonRpcResponse,
    decode_line,
    encode_message,
)


def test_encode_decode_request():
    req = JsonRpcRequest(id=1, method="system.ping", params={"timestamp": 12345.0})
    encoded = encode_message(req)
    assert encoded.endswith("\n")

    decoded = decode_line(encoded)
    assert decoded.id == 1
    assert decoded.method == "system.ping"
    assert decoded.params == {"timestamp": 12345.0}
    assert not decoded.is_notification


def test_decode_notification():
    line = '{"jsonrpc": "2.0", "method": "build.progress", "params": {"step": 2}}\n'
    decoded = decode_line(line)
    assert decoded.id is None
    assert decoded.is_notification
    assert decoded.method == "build.progress"
    assert decoded.params == {"step": 2}


def test_decode_invalid_json():
    with pytest.raises(ValueError, match="Invalid JSON"):
        decode_line("not-json\n")


def test_decode_invalid_structure():
    with pytest.raises(ValueError, match="Invalid JSON-RPC 2.0 request structure"):
        decode_line('{"not_rpc": true}\n')

    with pytest.raises(ValueError, match="Invalid JSON-RPC 2.0 request structure"):
        decode_line('{"jsonrpc": "1.0", "method": "test"}\n')


def test_decode_empty_line():
    with pytest.raises(ValueError, match="Empty line"):
        decode_line("   \n")


def test_encode_response_and_error():
    resp = JsonRpcResponse(id="req-123", result={"status": "ok"})
    encoded_resp = encode_message(resp)
    assert '"id":"req-123"' in encoded_resp or '"id": "req-123"' in encoded_resp
    assert '"result":{"status":"ok"}' in encoded_resp or '"status": "ok"' in encoded_resp

    err = JsonRpcErrorResponse(
        id=2,
        error=JsonRpcErrorObject(
            code=JsonRpcErrorCode.METHOD_NOT_FOUND,
            message="Method not found",
        ),
    )
    encoded_err = encode_message(err)
    assert "-32601" in encoded_err
    assert "Method not found" in encoded_err
