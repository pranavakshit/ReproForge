"""JSON-RPC 2.0 framing and schema definitions for local JSONL IPC."""

from enum import IntEnum
import json
from typing import Any, Dict, Literal, Optional, Union
from pydantic import BaseModel, Field


class JsonRpcErrorCode(IntEnum):
    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603

    # Application-specific error codes (-32000 to -32099)
    SERVER_SHUTDOWN = -32000
    BUILD_NOT_FOUND = -32001
    BUILD_ALREADY_FINISHED = -32002
    OPERATION_CANCELLED = -32003
    SERVICE_UNAVAILABLE = -32004


class JsonRpcErrorObject(BaseModel):
    code: int
    message: str
    data: Optional[Any] = None


class JsonRpcRequest(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: Optional[Union[str, int]] = None
    method: str
    params: Optional[Dict[str, Any]] = Field(default_factory=dict)

    @property
    def is_notification(self) -> bool:
        return self.id is None


class JsonRpcResponse(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: Optional[Union[str, int]] = None
    result: Optional[Any] = None


class JsonRpcErrorResponse(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: Optional[Union[str, int]] = None
    error: JsonRpcErrorObject


class JsonRpcNotification(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    method: str
    params: Optional[Dict[str, Any]] = Field(default_factory=dict)


def encode_message(msg: Union[JsonRpcResponse, JsonRpcErrorResponse, JsonRpcNotification, JsonRpcRequest]) -> str:
    """Encode a JSON-RPC message into a single-line string with trailing newline."""
    return msg.model_dump_json(exclude_none=False) + "\n"


def decode_line(line: str) -> JsonRpcRequest:
    """Decode a single line of JSON-RPC text into a JsonRpcRequest.

    Raises:
        ValueError: if the line is not valid JSON or does not conform to JSON-RPC 2.0.
    """
    stripped = line.strip()
    if not stripped:
        raise ValueError("Empty line")

    try:
        raw = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {exc}") from exc

    if not isinstance(raw, dict) or raw.get("jsonrpc") != "2.0" or "method" not in raw:
        raise ValueError("Invalid JSON-RPC 2.0 request structure")

    return JsonRpcRequest.model_validate(raw)
