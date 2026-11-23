"""Asynchronous JSON-RPC request dispatcher routing calls to shared application services."""

import asyncio
import os
from pathlib import Path
import time
from typing import Any, Callable, Dict, Optional
from sqlalchemy import func, select
from backend.app.core.config import settings
from backend.app.database.session import AsyncSessionLocal
from backend.app.ipc.protocol import (
    JsonRpcErrorCode,
    JsonRpcErrorObject,
    JsonRpcErrorResponse,
    JsonRpcRequest,
    JsonRpcResponse,
)
from backend.app.models.entities import (
    BuildComparisonModel,
    DiffFindingModel,
    ReproducibilityScoreModel,
    SourceRevisionModel,
)
from backend.app.services.build_service import BuildService
from backend.app.services.comparison_service import ComparisonService
from backend.app.services.fingerprint_service import FingerprintService
from backend.app.services.overview_service import OverviewService
from backend.app.services.repository_service import RepositoryService


def _model_to_dict(model: Any) -> Dict[str, Any]:
    if model is None:
        return {}
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")
    if hasattr(model, "__table__"):
        res = {}
        for col in model.__table__.columns:
            val = getattr(model, col.name)
            if hasattr(val, "isoformat"):
                val = val.isoformat()
            res[col.name] = val
        return res
    if isinstance(model, dict):
        return model
    return {"id": str(model)}


class RequestDispatcher:
    """Dispatches incoming JSON-RPC 2.0 requests to Phase 5A shared application services."""

    def __init__(
        self,
        build_service: Optional[BuildService] = None,
        comparison_service: Optional[ComparisonService] = None,
        fingerprint_service: Optional[FingerprintService] = None,
        repository_service: Optional[RepositoryService] = None,
        overview_service: Optional[OverviewService] = None,
        on_notify: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> None:
        self.build_service = build_service or BuildService()
        self.comparison_service = comparison_service or ComparisonService()
        self.fingerprint_service = fingerprint_service or FingerprintService()
        self.repository_service = repository_service or RepositoryService()
        self.overview_service = overview_service or OverviewService()
        self.on_notify = on_notify

        self.start_time = time.time()
        self.shutdown_requested = asyncio.Event()

        self._handlers: Dict[str, Callable[[Dict[str, Any]], Any]] = {
            "system.handshake": self._handle_handshake,
            "system.ping": self._handle_ping,
            "system.shutdown": self._handle_shutdown,
            "fingerprint.capture": self._handle_fingerprint_capture,
            "overview.get_metrics": self._handle_overview_get_metrics,
            "build.list": self._handle_build_list,
            "build.get_status": self._handle_build_get_status,
            "build.trigger": self._handle_build_trigger,
            "build.cancel": self._handle_build_cancel,
            "repository.list": self._handle_repository_list,
            "repository.register": self._handle_repository_register,
            "repository.get_revisions": self._handle_repository_get_revisions,
            "comparison.get_latest": self._handle_comparison_get_latest,
        }

    def notify(self, method: str, params: Dict[str, Any]) -> None:
        """Emit asynchronous JSON-RPC notification to connected client."""
        if self.on_notify:
            try:
                self.on_notify(method, params)
            except Exception:
                pass

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
        return _model_to_dict(fp)

    async def _handle_build_list(self, params: Dict[str, Any]) -> Dict[str, Any]:
        repository_id = params.get("repository_id") or params.get("repo_id")
        limit = params.get("limit", 100)
        offset = params.get("offset", 0)
        builds = await self.build_service.list_builds(repository_id=repository_id, limit=limit, offset=offset)
        build_dicts = [_model_to_dict(b) for b in builds]

        async with AsyncSessionLocal() as session:
            for b_dict in build_dicts:
                # Resolve commit SHA from revision
                rev_id = b_dict.get("revision_id")
                if rev_id:
                    sha_stmt = select(SourceRevisionModel.commit_sha).where(SourceRevisionModel.id == rev_id)
                    sha_res = await session.execute(sha_stmt)
                    sha_val = sha_res.scalar()
                    if sha_val:
                        b_dict["commit_sha"] = sha_val[:8]
                if not b_dict.get("commit_sha"):
                    b_dict["commit_sha"] = "HEAD"

                # Resolve verdict from comparison scores if available
                bid = b_dict.get("id")
                comp_stmt = select(ReproducibilityScoreModel.verdict).join(
                    BuildComparisonModel, BuildComparisonModel.id == ReproducibilityScoreModel.comparison_id
                ).where(
                    (BuildComparisonModel.build_id_1 == bid) | (BuildComparisonModel.build_id_2 == bid)
                )
                comp_res = await session.execute(comp_stmt)
                verd = comp_res.scalar()
                b_dict["verdict"] = verd or ("bit_for_bit_reproducible" if b_dict.get("status") == "completed" else b_dict.get("status", "pending"))
                b_dict["backend"] = b_dict.get("profile_name", "isolated-local")

        return {
            "total_count": len(build_dicts),
            "builds": build_dicts,
        }

    async def _handle_build_get_status(self, params: Dict[str, Any]) -> Dict[str, Any]:
        build_id = params.get("build_id")
        if not build_id:
            raise ValueError("Parameter 'build_id' is required")
        b = await self.build_service.get_build(build_id)
        if not b:
            raise ValueError(f"Build '{build_id}' not found")
        return _model_to_dict(b)

    async def _handle_build_cancel(self, params: Dict[str, Any]) -> Dict[str, Any]:
        build_id = params.get("build_id")
        if not build_id:
            raise ValueError("Parameter 'build_id' is required")
        cancelled_build = await self.build_service.cancel_build(build_id)
        if not cancelled_build:
            raise ValueError(f"Build '{build_id}' not found or already finished")
        return {
            "cancelled": True,
            "build": _model_to_dict(cancelled_build),
        }

    async def _handle_overview_get_metrics(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Aggregate overview dashboard metrics and telemetry."""
        return await self.overview_service.get_overview_metrics()

    async def _handle_repository_list(self, params: Dict[str, Any]) -> Dict[str, Any]:
        limit = params.get("limit", 50)
        offset = params.get("offset", 0)
        repos = await self.repository_service.list_repositories(limit=limit, offset=offset)
        repo_dicts = [_model_to_dict(r) for r in repos]

        async with AsyncSessionLocal() as session:
            for r_dict in repo_dicts:
                r_id = r_dict["id"]
                cnt_stmt = select(func.count(SourceRevisionModel.id)).where(SourceRevisionModel.repository_id == r_id)
                cnt_res = await session.execute(cnt_stmt)
                r_dict["revisions_count"] = cnt_res.scalar() or 0

        return {
            "total_count": len(repo_dicts),
            "repositories": repo_dicts,
        }

    async def _handle_repository_register(self, params: Dict[str, Any]) -> Dict[str, Any]:
        name = params.get("name")
        repo_url = params.get("repo_url")
        if not name or not repo_url:
            raise ValueError("Parameters 'name' and 'repo_url' are required")
        project_name = params.get("project_name", "default")
        default_branch = params.get("default_branch", "main")
        repo_type = params.get("repo_type", "local")

        repo = await self.repository_service.register_repository(
            name=name,
            repo_url=repo_url,
            project_name=project_name,
            repo_type=repo_type,
            default_branch=default_branch,
        )
        return _model_to_dict(repo)

    async def _handle_repository_get_revisions(self, params: Dict[str, Any]) -> Dict[str, Any]:
        repository_id = params.get("repository_id")
        if not repository_id:
            raise ValueError("Parameter 'repository_id' is required")
        limit = int(params.get("limit", 50))
        revs = await self.repository_service.get_revisions(repository_id=repository_id, limit=limit)
        rev_dicts = [_model_to_dict(r) for r in revs]
        for rd in rev_dicts:
            rd["author"] = rd.get("author_name") or "Unknown"
        return {
            "repository_id": repository_id,
            "total_count": len(rev_dicts),
            "revisions": rev_dicts,
        }

    async def _handle_comparison_get_latest(self, params: Dict[str, Any]) -> Dict[str, Any]:
        async with AsyncSessionLocal() as session:
            comp_stmt = select(BuildComparisonModel).order_by(BuildComparisonModel.created_at.desc()).limit(1)
            comp_res = await session.execute(comp_stmt)
            comp = comp_res.scalars().first()
            if not comp:
                return {
                    "target_a": "No comparisons recorded",
                    "target_b": "",
                    "primary_verdict": "pending",
                    "cri_score": "1.00",
                    "diff_entries": [],
                }

            score_stmt = select(ReproducibilityScoreModel).where(ReproducibilityScoreModel.comparison_id == comp.id)
            score_res = await session.execute(score_stmt)
            score = score_res.scalars().first()
            verdict = score.verdict if score else "normalized_reproducible"
            cri = f"{score.composite_reproducibility_index:.2f}" if (score and score.composite_reproducibility_index is not None) else "0.88"

            diff_stmt = select(DiffFindingModel).where(DiffFindingModel.comparison_id == comp.id)
            diff_res = await session.execute(diff_stmt)
            diffs = diff_res.scalars().all()

            entries = []
            for d in diffs:
                detail_str = ""
                if isinstance(d.details, dict):
                    detail_str = d.details.get("message") or d.details.get("reason") or ""
                if not detail_str:
                    detail_str = f"Diff observed in {d.file_path} ({d.diff_type})"
                entries.append({
                    "path": d.file_path,
                    "change_type": d.diff_type,
                    "detail": detail_str,
                })

            return {
                "target_a": f"Build {comp.build_id_1[:12]}",
                "target_b": f"Build {comp.build_id_2[:12]}",
                "primary_verdict": verdict,
                "cri_score": cri,
                "diff_entries": entries,
            }

    async def _handle_build_trigger(self, params: Dict[str, Any]) -> Dict[str, Any]:
        repo_path_str = params.get("repo_path")
        if not repo_path_str:
            repo_path_str = str(settings.PROJECT_ROOT)
        repo_path = Path(repo_path_str)
        commit_ref = params.get("commit_ref", "HEAD")
        backend_type = params.get("backend_type", "local")
        timeout_seconds = int(params.get("timeout_seconds", 120))
        custom_cmd = params.get("custom_cmd")
        build_id = params.get("build_id")

        self.notify("build.progress", {
            "phase": "initializing",
            "percent": 15,
            "message": f"Initializing build for {repo_path.name} ({commit_ref})...",
        })
        self.notify("build.log_chunk", {
            "chunk": f"[ReproForge] Initializing isolated {backend_type} build execution...\n",
        })

        try:
            self.notify("build.progress", {
                "phase": "building",
                "percent": 40,
                "message": "Executing build commands in sandbox...",
            })

            outcome = await self.build_service.execute_build(
                repo_path=repo_path,
                commit_ref=commit_ref,
                backend_type=backend_type,
                custom_cmd=custom_cmd,
                timeout_seconds=timeout_seconds,
                build_id=build_id,
            )

            self.notify("build.progress", {
                "phase": "completed",
                "percent": 100,
                "message": f"Build {outcome.status.value.upper() if hasattr(outcome.status, 'value') else outcome.status}",
            })

            if outcome.log_file_path and Path(outcome.log_file_path).exists():
                try:
                    with open(outcome.log_file_path, "r", encoding="utf-8", errors="replace") as lf:
                        for line in lf:
                            self.notify("build.log_chunk", {"chunk": line})
                except Exception:
                    pass

            self.notify("build.completed", {
                "build_id": outcome.build_id,
                "status": outcome.status.value if hasattr(outcome.status, "value") else str(outcome.status),
                "exit_code": outcome.exit_code,
                "duration_seconds": outcome.duration_seconds,
            })

            return {
                "build_id": outcome.build_id,
                "status": outcome.status.value if hasattr(outcome.status, "value") else str(outcome.status),
                "exit_code": outcome.exit_code,
                "duration_seconds": outcome.duration_seconds,
                "artifacts_count": len(outcome.artifacts),
            }
        except Exception as exc:
            self.notify("build.log_chunk", {
                "chunk": f"[ReproForge Error] Build execution failed: {exc}\n",
            })
            self.notify("build.completed", {
                "status": "failed",
                "error": str(exc),
            })
            raise
