"""ReproForge Local Structured JSONL IPC Package."""

from backend.app.ipc.protocol import (
    JsonRpcRequest,
    JsonRpcResponse,
    JsonRpcErrorObject,
    JsonRpcErrorResponse,
    JsonRpcNotification,
    JsonRpcErrorCode,
    encode_message,
    decode_line,
)

__all__ = [
    "JsonRpcRequest",
    "JsonRpcResponse",
    "JsonRpcErrorObject",
    "JsonRpcErrorResponse",
    "JsonRpcNotification",
    "JsonRpcErrorCode",
    "encode_message",
    "decode_line",
]
