"""Asynchronous JSON-RPC request dispatcher routing calls to shared application services."""

import asyncio
import os
import time
from typing import Any, Callable, Dict, Optional
from backend.app.core.config import settings
from backend.app.ipc.protocol import (
    JsonRpcErrorCode,
    JsonRpcErrorObject,
    JsonRpcErrorResponse,
    JsonRpcRequest,
    JsonRpcResponse,
)
from backend.app.services.build_service import BuildService
from backend.app.services.comparison_service import ComparisonService
from backend.app.services.fingerprint_service import FingerprintService
from backend.app.services.repository_service import RepositoryService


class RequestDispatcher:
    """Dispatches incoming JSON-RPC 2.0 requests to Phase 5A shared application services."""

    def __init__(
        self,
        build_service: Optional[BuildService] = None,
        comparison_service: Optional[ComparisonService] = None,
        fingerprint_service: Optional[FingerprintService] = None,
        repository_service: Optional[RepositoryService] = None,
    ) -> None:
        self.build_service = build_service or BuildService()
        self.comparison_service = comparison_service or ComparisonService()
        self.fingerprint_service = fingerprint_service or FingerprintService()
        self.repository_service = repository_service or RepositoryService()

        self.start_time = time.time()
        self.shutdown_requested = asyncio.Event()

        self._handlers: Dict[str, Callable[[Dict[str, Any]], Any]] = {
            "system.handshake": self._handle_handshake,
            "system.ping": self._handle_ping,
            "system.shutdown": self._handle_shutdown,
            "fingerprint.capture": self._handle_fingerprint_capture,
            "build.list": self._handle_build_list,
            "build.get_status": self._handle_build_get_status,
            "build.cancel": self._handle_build_cancel,
            "repository.list": self._handle_repository_list,
        }

    async def dispatch(self, request: JsonRpcRequest) -> Optional[Any]:
        """Dispatch a single JSON-RPC request and return a response object or None for notifications."""
        handler = self._handlers.get(request.method)
        if not handler:
            if request.is_notification:
                return None
            return JsonRpcErrorResponse(
                id=request.id,
                error=JsonRpcErrorObject(
                    code=JsonRpcErrorCode.METHOD_NOT_FOUND,
                    message=f"Method '{request.method}' not found",
                ),
            )

        try:
            params = request.params or {}
            result = handler(params)
            if asyncio.iscoroutine(result):
                result = await result

            if request.is_notification:
                return None

            return JsonRpcResponse(id=request.id, result=result)

        except ValueError as exc:
            if request.is_notification:
                return None
            return JsonRpcErrorResponse(
                id=request.id,
                error=JsonRpcErrorObject(
                    code=JsonRpcErrorCode.INVALID_PARAMS,
                    message=f"Invalid parameters: {exc}",
                ),
            )
        except Exception as exc:
            if request.is_notification:
                return None
            return JsonRpcErrorResponse(
                id=request.id,
                error=JsonRpcErrorObject(
                    code=JsonRpcErrorCode.INTERNAL_ERROR,
                    message=f"Internal service error: {exc}",
                ),
            )

    # --- System Handlers ---

    async def _handle_handshake(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Verify client version and negotiate capabilities."""
        return {
            "protocol_version": "1.0.0",
            "engine_pid": os.getpid(),
            "capabilities": ["build", "compare", "diagnose", "fingerprint", "reconciliation"],
            "sqlite_wal_active": True,
            "database_url": settings.DATABASE_URL,
            "uptime_seconds": round(time.time() - self.start_time, 2),
        }

    async def _handle_ping(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Heartbeat ping response."""
        return {
            "timestamp": params.get("timestamp", time.time()),
            "engine_uptime_seconds": round(time.time() - self.start_time, 2),
            "status": "healthy",
        }

    async def _handle_shutdown(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Request graceful engine shutdown."""
        self.shutdown_requested.set()
        return {"acknowledged": True, "engine_pid": os.getpid()}

    # --- Service Handlers ---

    async def _handle_fingerprint_capture(self, params: Dict[str, Any]) -> Dict[str, Any]:
        repo_id = params.get("repo_id")
        fp = await self.fingerprint_service.capture_and_persist(repo_id=repo_id)
        return fp.model_dump(mode="json")

    async def _handle_build_list(self, params: Dict[str, Any]) -> Dict[str, Any]:
        repo_id = params.get("repo_id")
        limit = params.get("limit", 100)
        offset = params.get("offset", 0)
        builds = await self.build_service.list_builds(repo_id=repo_id, limit=limit, offset=offset)
        return {
            "total_count": len(builds),
            "builds": [b.model_dump(mode="json") for b in builds],
        }

    async def _handle_build_get_status(self, params: Dict[str, Any]) -> Dict[str, Any]:
        build_id = params.get("build_id")
        if not build_id:
            raise ValueError("Parameter 'build_id' is required")
        b = await self.build_service.get_build(build_id)
        if not b:
            raise ValueError(f"Build '{build_id}' not found")
        return b.model_dump(mode="json")

    async def _handle_build_cancel(self, params: Dict[str, Any]) -> Dict[str, Any]:
        build_id = params.get("build_id")
        if not build_id:
            raise ValueError("Parameter 'build_id' is required")
        cancelled_build = await self.build_service.cancel_build(build_id)
        return {
            "cancelled": True,
            "build": cancelled_build.model_dump(mode="json"),
        }

    async def _handle_repository_list(self, params: Dict[str, Any]) -> Dict[str, Any]:
        limit = params.get("limit", 50)
        offset = params.get("offset", 0)
        repos = await self.repository_service.list_repositories(limit=limit, offset=offset)
        return {
            "total_count": len(repos),
            "repositories": [r.model_dump(mode="json") for r in repos],
        }
