"""Asynchronous JSON-RPC request dispatcher routing calls to shared application services."""

import asyncio
import json
import os
from pathlib import Path
import time
import uuid
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
    ArtifactItemModel,
    BuildComparisonModel,
    BuildExecutionModel,
    DiagnosticFindingModel,
    DiffFindingModel,
    RepositoryModel,
    ReproducibilityScoreModel,
    SourceRevisionModel,
)
from backend.app.services.build_service import BuildService
from backend.app.reporting.exporter import ReportExporter
from backend.app.services.causality_graph_service import CausalityGraphService
from backend.app.services.comparison_service import ComparisonService
from backend.app.services.fingerprint_service import FingerprintService
from backend.app.services.overview_service import OverviewService
from backend.app.services.passport_service import PassportService
from backend.app.services.perturbation_service import PerturbationService
from backend.app.services.remediation_engine import RemediationEngine
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
        causality_graph_service: Optional[CausalityGraphService] = None,
        perturbation_service: Optional[PerturbationService] = None,
        passport_service: Optional[PassportService] = None,
        remediation_engine: Optional[RemediationEngine] = None,
        on_notify: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> None:
        self.build_service = build_service or BuildService()
        self.comparison_service = comparison_service or ComparisonService()
        self.fingerprint_service = fingerprint_service or FingerprintService()
        self.repository_service = repository_service or RepositoryService()
        self.overview_service = overview_service or OverviewService()
        self.causality_graph_service = causality_graph_service or CausalityGraphService()
        self.perturbation_service = perturbation_service or PerturbationService()
        self.passport_service = passport_service or PassportService()
        self.remediation_engine = remediation_engine or RemediationEngine()
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
            "build.get_details": self._handle_build_get_details,
            "build.trigger": self._handle_build_trigger,
            "build.verify_pair": self._handle_build_verify_pair,
            "build.cancel": self._handle_build_cancel,
            "repository.list": self._handle_repository_list,
            "repository.register": self._handle_repository_register,
            "repository.scan_folder": self._handle_repository_scan_folder,
            "repository.register_batch": self._handle_repository_register_batch,
            "repository.autoscan": self._handle_repository_autoscan,
            "repository.get_revisions": self._handle_repository_get_revisions,
            "comparison.get_latest": self._handle_comparison_get_latest,
            "comparison.list": self._handle_comparison_list,
            "comparison.get_details": self._handle_comparison_get_details,
            "comparison.compare_latest": self._handle_comparison_compare_latest,
            "comparison.compare_paths": self._handle_comparison_compare_paths,
            "comparison.export_report": self._handle_comparison_export_report,
            "causality.get_graph": self._handle_causality_get_graph,
            "perturbation.params": self._handle_perturbation_params,
            "perturbation.run": self._handle_perturbation_run,
            "perturbation.list": self._handle_perturbation_list,
            "passport.get": self._handle_passport_get,
            "passport.export": self._handle_passport_export,
            "remediation.list_proposals": self._handle_remediation_list_proposals,
            "remediation.approve": self._handle_remediation_approve,
            "remediation.verify_rebuild": self._handle_remediation_verify_rebuild,
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
        import json as _json

        repo_id = params.get("repo_id")
        fp = await self.fingerprint_service.capture_and_persist(repo_id=repo_id)
        data = _model_to_dict(fp)
        data["json_pretty"] = _json.dumps(data, indent=2, default=str)
        return data

    async def _handle_build_list(self, params: Dict[str, Any]) -> Dict[str, Any]:
        repository_id = params.get("repository_id") or params.get("repo_id")
        limit = params.get("limit", 100)
        offset = params.get("offset", 0)
        builds = await self.build_service.list_builds(repository_id=repository_id, limit=limit, offset=offset)
        build_dicts = [_model_to_dict(b) for b in builds]

        async with AsyncSessionLocal() as session:
            repo_names: Dict[str, str] = {}
            for r in (await session.execute(select(RepositoryModel))).scalars().all():
                repo_names[r.id] = r.name

            for b_dict in build_dicts:
                bid = b_dict.get("id", "")
                b_dict["build_id"] = bid
                b_dict["repo_name"] = repo_names.get(b_dict.get("repository_id", ""), "unknown")

                # Resolve commit SHA from revision
                rev_id = b_dict.get("revision_id")
                if rev_id:
                    sha_val = (
                        await session.execute(
                            select(SourceRevisionModel.commit_sha).where(SourceRevisionModel.id == rev_id)
                        )
                    ).scalar()
                    if sha_val:
                        b_dict["commit_sha"] = sha_val[:8]
                if not b_dict.get("commit_sha"):
                    b_dict["commit_sha"] = "HEAD"

                # Resolve primary artifact SHA-256
                art_sha = (
                    await session.execute(
                        select(ArtifactItemModel.strict_sha256).where(
                            ArtifactItemModel.build_execution_id == bid
                        )
                    )
                ).scalar()
                b_dict["primary_hash"] = f"sha256:{art_sha[:12]}" if art_sha else "—"

                # Resolve verdict from comparison scores if available
                comp_stmt = select(ReproducibilityScoreModel.verdict).join(
                    BuildComparisonModel, BuildComparisonModel.id == ReproducibilityScoreModel.comparison_id
                ).where(
                    (BuildComparisonModel.build_id_1 == bid) | (BuildComparisonModel.build_id_2 == bid)
                )
                verd = (await session.execute(comp_stmt)).scalar()
                b_dict["verdict"] = (verd or ("UNCOMPARED" if b_dict.get("status") == "completed" else b_dict.get("status", "pending"))).upper()
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

    async def _handle_build_get_details(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Return persisted artifacts (real SHA-256 digests) and full execution log for a build."""
        build_id = params.get("build_id")
        if not build_id:
            raise ValueError("Parameter 'build_id' is required")
        b = await self.build_service.get_build(build_id)
        if not b:
            raise ValueError(f"Build '{build_id}' not found")

        data = _model_to_dict(b)
        async with AsyncSessionLocal() as session:
            art_stmt = select(ArtifactItemModel).where(ArtifactItemModel.build_execution_id == build_id)
            artifacts = (await session.execute(art_stmt)).scalars().all()
            data["artifacts"] = [_model_to_dict(a) for a in artifacts]
            data["primary_sha256"] = artifacts[0].strict_sha256 if artifacts else ""

        log_text = ""
        if b.log_file_path and Path(b.log_file_path).is_file():
            try:
                log_text = Path(b.log_file_path).read_text(encoding="utf-8", errors="replace")[-30_000:]
            except Exception:
                log_text = ""
        data["log_text"] = log_text
        return data

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

                scan_meta = r_dict.get("scan_metadata_json") or {}
                readiness = scan_meta.get("readiness", {}) if isinstance(scan_meta, dict) else {}
                detected = readiness.get("detected_systems") or []
                r_dict["build_system"] = ", ".join(detected[:3]) if detected else "unknown"
                r_dict["readiness_status"] = readiness.get("readiness_status", "unscanned")
                r_dict["readiness_warnings"] = readiness.get("readiness_warnings", [])

                # Expose CLI `detect` parity details statically on local repos
                repo_path_obj = Path(r_dict.get("repo_url", ""))
                primary_cfg = readiness.get("primary_config_file", "")
                lock_path = readiness.get("lockfile_path", "")
                dep_count = readiness.get("declared_dependencies_count", 0)
                suggested_cmd = readiness.get("suggested_build_command", "")
                if (not suggested_cmd) and repo_path_obj.exists() and repo_path_obj.is_dir():
                    try:
                        det_list = self.build_service.detector.detect(repo_path_obj)
                        if det_list:
                            first = det_list[0]
                            primary_cfg = first.primary_config_file
                            lock_path = first.lockfile_path or ("Yes" if first.has_lockfile else "None")
                            dep_count = sum(s.declared_dependencies_count for s in det_list)
                            suggested_cmd = first.suggested_build_command
                    except Exception:
                        pass
                r_dict["primary_config_file"] = primary_cfg or "—"
                r_dict["lockfile_path"] = lock_path or "None"
                r_dict["declared_dependencies_count"] = int(dep_count or 0)
                r_dict["suggested_build_command"] = suggested_cmd or "auto"

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
        fetch_online = bool(params.get("fetch_online_metadata", False))

        kwargs: Dict[str, Any] = {
            "name": name,
            "repo_url": repo_url,
            "project_name": project_name,
            "repo_type": repo_type,
            "default_branch": default_branch,
        }
        if fetch_online:
            kwargs["fetch_online_metadata"] = True

        repo = await self.repository_service.register_repository(**kwargs)
        return _model_to_dict(repo)

    async def _handle_repository_scan_folder(self, params: Dict[str, Any]) -> Dict[str, Any]:
        folder_path = params.get("folder_path")
        if not folder_path:
            raise ValueError("Parameter 'folder_path' is required")
        return await self.repository_service.discover_folder_candidates(Path(folder_path))

    async def _handle_repository_register_batch(self, params: Dict[str, Any]) -> Dict[str, Any]:
        items = params.get("items") or []
        project_name = params.get("project_name", "Workspace Imports")
        fetch_online = bool(params.get("fetch_online_metadata", False))
        repos = await self.repository_service.register_batch(
            items=items,
            project_name=project_name,
            fetch_online_metadata=fetch_online,
        )
        return {"registered_count": len(repos), "repositories": [_model_to_dict(r) for r in repos]}

    async def _handle_repository_autoscan(self, params: Dict[str, Any]) -> Dict[str, Any]:
        repository_id = params.get("repository_id")
        if not repository_id:
            raise ValueError("Parameter 'repository_id' is required")
        fetch_online = bool(params.get("fetch_online_metadata", True))
        repo = await self.repository_service.autoscan_repository(repository_id, fetch_online_metadata=fetch_online)
        if not repo:
            raise ValueError(f"Repository '{repository_id}' not found")
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
            rd["sha"] = rd.get("commit_sha") or "HEAD"
            rd["message"] = rd.get("commit_message") or "(no commit message)"
            rd["date"] = str(rd.get("committed_at") or rd.get("created_at") or "")[:10]
        return {
            "repository_id": repository_id,
            "total_count": len(rev_dicts),
            "revisions": rev_dicts,
        }

    async def _handle_comparison_get_latest(self, params: Dict[str, Any]) -> Dict[str, Any]:
        repository_id = params.get("repository_id")
        async with AsyncSessionLocal() as session:
            if repository_id:
                from sqlalchemy import or_
                bld_ids = [
                    r[0]
                    for r in (
                        await session.execute(
                            select(BuildExecutionModel.id).where(
                                BuildExecutionModel.repository_id == repository_id
                            )
                        )
                    ).all()
                ]
                if not bld_ids:
                    return {"id": None, "verdict": "UNCOMPARED", "diff_entries": [], "diagnostic_findings": []}
                comp_stmt = (
                    select(BuildComparisonModel)
                    .where(
                        or_(
                            BuildComparisonModel.build_id_1.in_(bld_ids),
                            BuildComparisonModel.build_id_2.in_(bld_ids),
                        )
                    )
                    .order_by(BuildComparisonModel.created_at.desc())
                    .limit(1)
                )
            else:
                comp_stmt = select(BuildComparisonModel).order_by(BuildComparisonModel.created_at.desc()).limit(1)

            comp_res = await session.execute(comp_stmt)
            comp = comp_res.scalars().first()
            if not comp:
                return {
                    "id": None,
                    "build_a_id": "No comparisons yet for this repo",
                    "build_b_id": "Run 'Verify Reproducibility (2x Build + Compare)'",
                    "verdict": "UNCOMPARED",
                    "content_agreement_ratio": 0.0,
                    "metadata_agreement_ratio": 0.0,
                    "environment_consistency_score": 0.0,
                    "composite_reproducibility_index": 0.0,
                    "diff_entries": [],
                    "diagnostic_findings": [],
                }

            score_stmt = select(ReproducibilityScoreModel).where(ReproducibilityScoreModel.comparison_id == comp.id)
            score = (await session.execute(score_stmt)).scalars().first()
            verdict_raw = (
                score.verdict
                if score
                else ("bit_for_bit_reproducible" if comp.exact_match else ("normalized_reproducible" if comp.normalized_match else "non_reproducible"))
            )
            verdict = verdict_raw.upper()

            diffs = (
                await session.execute(select(DiffFindingModel).where(DiffFindingModel.comparison_id == comp.id))
            ).scalars().all()

            entries = []
            for d in diffs:
                det = d.details if isinstance(d.details, dict) else {}
                snippet = det.get("diff_snippet") or det.get("message") or det.get("reason") or f"Delta observed in {d.file_path} ({d.diff_type})"
                entries.append({
                    "relative_path": d.file_path,
                    "diff_type": d.diff_type.upper(),
                    "size_a": int(det.get("size_1", det.get("size_a", 0)) or 0),
                    "size_b": int(det.get("size_2", det.get("size_b", 0)) or 0),
                    "diff_snippet": str(snippet),
                })

            diags = (
                await session.execute(select(DiagnosticFindingModel).where(DiagnosticFindingModel.comparison_id == comp.id))
            ).scalars().all()

            return {
                "id": comp.id,
                "build_a_id": comp.build_id_1,
                "build_b_id": comp.build_id_2,
                "verdict": verdict,
                "content_agreement_ratio": float(score.content_agreement_ratio) if score else 1.0,
                "metadata_agreement_ratio": float(score.metadata_agreement_ratio) if score else 1.0,
                "environment_consistency_score": float(score.environment_consistency_score) if score else 1.0,
                "composite_reproducibility_index": float(score.composite_reproducibility_index) if score else 0.0,
                "diff_entries": entries,
                "diagnostic_findings": [
                    {
                        "rule_id": f"RULE-{dg.category.upper().replace(' ', '_')}",
                        "severity": "HIGH" if dg.confidence.lower() == "high" else "MEDIUM",
                        "category": dg.category,
                        "title": dg.category.replace("_", " ").title(),
                        "epistemic_status": (
                            "EXPERIMENTALLY_VALIDATED"
                            if "experimentally_validated" in str(dg.evidence_trail).lower()
                            else ("OBSERVED" if "observed" in str(dg.evidence_trail).lower() else ("INFERRED" if dg.confidence.lower() == "high" else "HYPOTHESIS"))
                        ),
                        "summary": dg.root_cause_summary,
                        "suggested_remediation": dg.suggested_remediation,
                    }
                    for dg in diags
                ],
            }

    async def _handle_comparison_list(self, params: Dict[str, Any]) -> Dict[str, Any]:
        limit = params.get("limit", 20)
        async with AsyncSessionLocal() as session:
            stmt = select(BuildComparisonModel).order_by(BuildComparisonModel.created_at.desc()).limit(limit)
            res = await session.execute(stmt)
            comps = res.scalars().all()
            items = []
            for c in comps:
                score_stmt = select(ReproducibilityScoreModel).where(ReproducibilityScoreModel.comparison_id == c.id)
                score_res = await session.execute(score_stmt)
                score = score_res.scalars().first()
                items.append({
                    "id": c.id,
                    "build_id_1": c.build_id_1,
                    "build_id_2": c.build_id_2,
                    "exact_match": c.exact_match,
                    "normalized_match": c.normalized_match,
                    "verdict": score.verdict if score else ("bit_for_bit_reproducible" if c.exact_match else "normalized_reproducible"),
                    "cri": f"{score.composite_reproducibility_index:.2f}" if (score and score.composite_reproducibility_index is not None) else "0.88",
                    "created_at": c.created_at.isoformat() if c.created_at else "",
                })
            return {"total_count": len(items), "comparisons": items}

    async def _handle_comparison_get_details(self, params: Dict[str, Any]) -> Dict[str, Any]:
        comp_id = params.get("comparison_id")
        async with AsyncSessionLocal() as session:
            stmt = select(BuildComparisonModel).where(BuildComparisonModel.id == comp_id) if comp_id else select(BuildComparisonModel).order_by(BuildComparisonModel.created_at.desc()).limit(1)
            res = await session.execute(stmt)
            comp = res.scalars().first()
            if not comp:
                raise ValueError("No comparison found")

            score_stmt = select(ReproducibilityScoreModel).where(ReproducibilityScoreModel.comparison_id == comp.id)
            score_res = await session.execute(score_stmt)
            score = score_res.scalars().first()

            diff_stmt = select(DiffFindingModel).where(DiffFindingModel.comparison_id == comp.id)
            diff_res = await session.execute(diff_stmt)
            diffs = diff_res.scalars().all()

            diag_stmt = select(DiagnosticFindingModel).where(DiagnosticFindingModel.comparison_id == comp.id)
            diag_res = await session.execute(diag_stmt)
            diags = diag_res.scalars().all()

            return {
                "id": comp.id,
                "build_id_1": comp.build_id_1,
                "build_id_2": comp.build_id_2,
                "exact_match": comp.exact_match,
                "normalized_match": comp.normalized_match,
                "content_agreement_ratio": score.content_agreement_ratio if score else 1.0,
                "metadata_agreement_ratio": score.metadata_agreement_ratio if score else 1.0,
                "environment_consistency_score": score.environment_consistency_score if score else 1.0,
                "verdict": score.verdict if score else "normalized_reproducible",
                "cri": f"{score.composite_reproducibility_index:.2f}" if (score and score.composite_reproducibility_index is not None) else "0.88",
                "diff_entries": [{"path": d.file_path, "change_type": d.diff_type, "detail": (d.details.get("message") if isinstance(d.details, dict) else "") or f"Diff in {d.file_path}"} for d in diffs],
                "diagnostic_findings": [
                    {
                        "category": dg.category,
                        "confidence": dg.confidence,
                        "rule_id": f"RULE-{dg.category.upper().replace(' ', '_')}",
                        "message": dg.root_cause_summary,
                        "epistemic_status": (
                            "EXPERIMENTALLY_VALIDATED"
                            if "experimentally_validated" in str(dg.evidence_trail).lower()
                            else ("INFERRED" if dg.confidence.lower() == "high" else "HYPOTHESIS")
                        ),
                    }
                    for dg in diags
                ],
            }

    async def _handle_causality_get_graph(self, params: Dict[str, Any]) -> Dict[str, Any]:
        comp_id = params.get("comparison_id")
        repo_id = params.get("repository_id")
        if comp_id:
            graph = await self.causality_graph_service.get_graph_for_comparison(comp_id)
        else:
            graph = await self.causality_graph_service.get_latest_graph(repository_id=repo_id)
        if not graph:
            return {"nodes": [], "edges": []}
        return graph

    async def _handle_comparison_compare_latest(self, params: Dict[str, Any]) -> Dict[str, Any]:
        repository_id = params.get("repository_id")
        async with AsyncSessionLocal() as session:
            stmt = select(BuildExecutionModel).where(BuildExecutionModel.status == "completed")
            if repository_id:
                stmt = stmt.where(BuildExecutionModel.repository_id == repository_id)
            stmt = stmt.order_by(BuildExecutionModel.created_at.desc() if hasattr(BuildExecutionModel, "created_at") else BuildExecutionModel.started_at.desc()).limit(20)
            builds = list((await session.execute(stmt)).scalars().all())
            pair: list[tuple[str, Path]] = []
            for b in builds:
                art = (
                    await session.execute(
                        select(ArtifactItemModel).where(ArtifactItemModel.build_execution_id == b.id)
                    )
                ).scalars().first()
                if art:
                    p = settings.ARTIFACTS_DIR / b.id / art.relative_path
                    if p.exists():
                        pair.append((b.id, p))
                if len(pair) == 2:
                    break
            if len(pair) < 2:
                raise ValueError("Need at least 2 completed builds with captured artifacts to compare.")

        (b1, p1), (b2, p2) = pair[1], pair[0]
        outcome = await self.comparison_service.compare_artifacts(p1, p2, build_id_1=b1, build_id_2=b2)
        return {
            "comparison_id": outcome.comparison_id,
            "build_a_id": b1,
            "build_b_id": b2,
            "verdict": outcome.assessment.verdict.value.upper(),
            "cri": round(outcome.assessment.composite_reproducibility_index, 4),
        }

    async def _handle_comparison_compare_paths(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Compare any two artifact files or directories (full CLI `compare` parity)."""
        path_a = Path(str(params.get("path_a", "")).strip())
        path_b = Path(str(params.get("path_b", "")).strip())
        if not path_a.exists() or not path_b.exists():
            raise ValueError(f"Both comparison paths must exist on disk ('{path_a}' vs '{path_b}')")

        outcome = await self.comparison_service.compare_artifacts(
            path_a,
            path_b,
            build_id_1=params.get("build_id_1"),
            build_id_2=params.get("build_id_2"),
        )
        return {
            "comparison_id": outcome.comparison_id,
            "path_a": str(path_a),
            "path_b": str(path_b),
            "verdict": outcome.assessment.verdict.value.upper(),
            "cri": round(outcome.assessment.composite_reproducibility_index, 4),
            "exact_match": outcome.summary.exact_match,
            "normalized_match": outcome.summary.normalized_match,
        }

    async def _handle_comparison_export_report(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Export comparison diagnostics & diffs to JSON, CSV, or HTML report files."""
        comp_id = params.get("comparison_id")
        fmt = str(params.get("format", "html")).lower()
        if fmt not in {"json", "csv", "html"}:
            raise ValueError(f"Unsupported report format '{fmt}'; use json, csv, or html")

        details = await self._handle_comparison_get_latest(
            {"repository_id": params.get("repository_id")} if not comp_id else {}
        )
        if comp_id:
            details = await self._handle_comparison_get_details({"comparison_id": comp_id})

        if not details or not details.get("id"):
            raise ValueError("No completed comparison available to export")

        reports_dir = settings.ARTIFACTS_DIR / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        cid_short = str(details.get("id", "latest"))[:12]
        out_path = reports_dir / f"reproforge-report-{cid_short}.{fmt}"

        if fmt == "json":
            content = ReportExporter.comparison_to_json(details)
        elif fmt == "csv":
            content = ReportExporter.comparison_to_csv(details)
        else:
            content = ReportExporter.comparison_to_html(details)

        out_path.write_text(content, encoding="utf-8")
        return {
            "format": fmt,
            "file_path": str(out_path),
            "comparison_id": details.get("id"),
            "bytes": len(content.encode("utf-8")),
        }

    @staticmethod
    def _read_failure_tail(log_file_path: str, max_lines: int = 5) -> str:
        p = Path(log_file_path) if log_file_path else None
        if not p or not p.is_file():
            return ""
        try:
            lines = [ln.strip() for ln in p.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
            return " | ".join(lines[-max_lines:]) if lines else ""
        except Exception:
            return ""

    async def _handle_build_verify_pair(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Run two isolated builds back-to-back and compare them end-to-end."""
        repo_path_str = params.get("repo_path")
        repo_id = params.get("repository_id")
        if not repo_path_str and repo_id:
            repos = await self.repository_service.list_repositories()
            match = next((r for r in repos if r.id == repo_id), None)
            if match:
                repo_path_str = match.repo_url
        if not repo_path_str:
            repo_path_str = str(settings.BASE_DIR)

        repo_path = Path(repo_path_str)
        commit_ref = params.get("commit_ref", "HEAD")
        backend_type = params.get("backend_type", "local")
        dependency_mode = str(params.get("dependency_mode", "full")).strip() or "full"
        timeout_seconds = int(params.get("timeout_seconds", 300))
        custom_cmd = params.get("custom_cmd") or None

        bid_a = f"vrf-a-{uuid.uuid4().hex[:8]}"
        self.notify("build.progress", {
            "build_id": bid_a,
            "phase": "verify_pair",
            "percent": 10,
            "message": f"[1/3] Running Build A ({backend_type}, deps={dependency_mode}) for {repo_path.name} ({commit_ref})...",
        })

        out_a = await self.build_service.execute_build(
            repo_path=repo_path,
            commit_ref=commit_ref,
            backend_type=backend_type,
            custom_cmd=custom_cmd,
            timeout_seconds=timeout_seconds,
            build_id=bid_a,
            dependency_mode=dependency_mode,
            on_output_chunk=lambda line: self.notify("build.log_chunk", {"build_id": bid_a, "chunk": f"[Build A] {line}"}),
        )
        if out_a.exit_code != 0 or not out_a.artifacts:
            tail = self._read_failure_tail(out_a.log_file_path)
            detail_msg = f" — Tail: {tail}" if tail else ""
            raise RuntimeError(
                f"Verification Build A failed (exit={out_a.exit_code}, artifacts={len(out_a.artifacts)}){detail_msg}"
            )

        await asyncio.sleep(1.1)
        bid_b = f"vrf-b-{uuid.uuid4().hex[:8]}"
        self.notify("build.progress", {
            "build_id": bid_b,
            "phase": "verify_pair",
            "percent": 50,
            "message": f"[2/3] Running Build B ({backend_type}, deps={dependency_mode}) for {repo_path.name} ({commit_ref})...",
        })

        out_b = await self.build_service.execute_build(
            repo_path=repo_path,
            commit_ref=commit_ref,
            backend_type=backend_type,
            custom_cmd=custom_cmd,
            timeout_seconds=timeout_seconds,
            build_id=bid_b,
            dependency_mode=dependency_mode,
            on_output_chunk=lambda line: self.notify("build.log_chunk", {"build_id": bid_b, "chunk": f"[Build B] {line}"}),
        )
        if out_b.exit_code != 0 or not out_b.artifacts:
            tail = self._read_failure_tail(out_b.log_file_path)
            detail_msg = f" — Tail: {tail}" if tail else ""
            raise RuntimeError(
                f"Verification Build B failed (exit={out_b.exit_code}, artifacts={len(out_b.artifacts)}){detail_msg}"
            )

        self.notify("build.progress", {
            "build_id": bid_b,
            "phase": "comparing",
            "percent": 85,
            "message": "[3/3] Comparing Build A vs Build B & running RCA diagnostics...",
        })

        art_a = settings.ARTIFACTS_DIR / out_a.build_id / out_a.artifacts[0].relative_path
        art_b = settings.ARTIFACTS_DIR / out_b.build_id / out_b.artifacts[0].relative_path
        comp_outcome = await self.comparison_service.compare_artifacts(
            art_a,
            art_b,
            build_id_1=out_a.build_id,
            build_id_2=out_b.build_id,
        )
        verdict = comp_outcome.assessment.verdict.value.upper()
        cri = round(comp_outcome.assessment.composite_reproducibility_index, 4)

        self.notify("build.completed", {
            "build_id": out_b.build_id,
            "status": "completed",
            "primary_hash": out_b.artifacts[0].strict_sha256,
            "comparison_id": comp_outcome.comparison_id,
            "verdict": verdict,
        })
        return {
            "build_a_id": out_a.build_id,
            "build_b_id": out_b.build_id,
            "comparison_id": comp_outcome.comparison_id,
            "verdict": verdict,
            "cri": cri,
        }

    async def _handle_perturbation_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        repo_id = params.get("repository_id")
        return {
            "params": self.perturbation_service.list_default_parameters(),
            "campaigns": self.perturbation_service.load_campaigns(repository_id=repo_id),
        }

    async def _handle_perturbation_run(self, params: Dict[str, Any]) -> Dict[str, Any]:
        repo_id = params.get("repository_id")
        if not repo_id:
            repos = await self.repository_service.list_repositories()
            if not repos:
                raise ValueError("No tracked repositories available for perturbation campaign")
            repo_id = repos[0].id
        return await self.perturbation_service.run_campaign(
            repository_id=repo_id,
            commit_ref=params.get("commit_ref", "HEAD"),
            param_names=params.get("param_names"),
            execute_real_builder=bool(params.get("execute_real_builder", False)),
            param_overrides=params.get("param_overrides"),
        )

    async def _handle_perturbation_list(self, params: Dict[str, Any]) -> Dict[str, Any]:
        campaigns = self.perturbation_service.load_campaigns(repository_id=params.get("repository_id"))
        return {"campaigns": campaigns}

    async def _handle_passport_get(self, params: Dict[str, Any]) -> Dict[str, Any]:
        passport = await self.passport_service.generate_passport(
            repository_id=params.get("repository_id")
        )
        return {"passport": passport, "json_pretty": json.dumps(passport, indent=2)}

    async def _handle_passport_export(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return await self.passport_service.export_passport(
            repository_id=params.get("repository_id"),
            fmt=params.get("format", "json"),
        )

    async def _handle_remediation_list_proposals(self, params: Dict[str, Any]) -> Dict[str, Any]:
        proposals = await self.remediation_engine.list_proposals(
            repository_id=params.get("repository_id")
        )
        return {"proposals": proposals}

    async def _handle_remediation_approve(self, params: Dict[str, Any]) -> Dict[str, Any]:
        proposal_id = params.get("proposal_id")
        if not proposal_id:
            raise ValueError("proposal_id is required")
        return await self.remediation_engine.approve_and_verify(
            proposal_id=proposal_id,
            repository_id=params.get("repository_id"),
            approved=bool(params.get("approved", False)),
        )

    async def _handle_remediation_verify_rebuild(self, params: Dict[str, Any]) -> Dict[str, Any]:
        proposal_id = params.get("proposal_id")
        if not proposal_id:
            proposals = await self.remediation_engine.list_proposals(
                repository_id=params.get("repository_id")
            )
            eligible = next((p for p in proposals if p["tier"] != "LEVEL_3"), None)
            if not eligible:
                return {"status": "NO_ELIGIBLE_PROPOSALS", "verified": False}
            proposal_id = eligible["id"]
        return await self.remediation_engine.approve_and_verify(
            proposal_id=proposal_id,
            repository_id=params.get("repository_id"),
            approved=bool(params.get("approved", True)),
        )

    async def _handle_build_trigger(self, params: Dict[str, Any]) -> Dict[str, Any]:
        repo_path_str = params.get("repo_path")
        if not repo_path_str and params.get("repository_id"):
            repos = await self.repository_service.list_repositories()
            match = next((r for r in repos if r.id == params["repository_id"]), None)
            if match:
                repo_path_str = match.repo_url
        if not repo_path_str:
            repo_path_str = str(settings.BASE_DIR)
        repo_path = Path(repo_path_str)
        commit_ref = params.get("commit_ref", "HEAD")
        backend_type = params.get("backend_type", "local")
        dependency_mode = str(params.get("dependency_mode", "full")).strip() or "full"
        timeout_seconds = int(params.get("timeout_seconds", 300))
        custom_cmd = params.get("custom_cmd") or None
        build_id = params.get("build_id") or f"build-{uuid.uuid4().hex[:10]}"

        self.notify("build.progress", {
            "build_id": build_id,
            "phase": "initializing",
            "percent": 15,
            "message": f"Initializing build for {repo_path.name} ({commit_ref})...",
        })
        self.notify("build.log_chunk", {
            "build_id": build_id,
            "chunk": f"[ReproForge] Initializing {backend_type} build (deps={dependency_mode}, id={build_id})...\n",
        })

        streamed_chunks = False

        def on_chunk(line: str) -> None:
            nonlocal streamed_chunks
            streamed_chunks = True
            self.notify("build.log_chunk", {"build_id": build_id, "chunk": line})

        try:
            self.notify("build.progress", {
                "build_id": build_id,
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
                dependency_mode=dependency_mode,
                on_output_chunk=on_chunk,
            )

            status_val = outcome.status.value if hasattr(outcome.status, "value") else str(outcome.status)
            self.notify("build.progress", {
                "build_id": outcome.build_id,
                "phase": status_val,
                "percent": 100,
                "message": f"Build {status_val.upper()}",
            })

            if not streamed_chunks and outcome.log_file_path and Path(outcome.log_file_path).exists():
                try:
                    with open(outcome.log_file_path, "r", encoding="utf-8", errors="replace") as lf:
                        for line in lf:
                            self.notify("build.log_chunk", {"build_id": outcome.build_id, "chunk": line})
                except Exception:
                    pass

            self.notify("build.completed", {
                "build_id": outcome.build_id,
                "status": status_val,
                "exit_code": outcome.exit_code,
                "duration_seconds": outcome.duration_seconds,
            })

            return {
                "build_id": outcome.build_id,
                "status": status_val,
                "exit_code": outcome.exit_code,
                "duration_seconds": outcome.duration_seconds,
                "artifacts_count": len(outcome.artifacts),
            }
        except Exception as exc:
            self.notify("build.log_chunk", {
                "build_id": build_id,
                "chunk": f"[ReproForge Error] Build execution failed: {exc}\n",
            })
            self.notify("build.completed", {
                "build_id": build_id,
                "status": "failed",
                "error": str(exc),
            })
            raise
