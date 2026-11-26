from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel

from backend.app.core.config import settings
from backend.app.database.session import init_db
from backend.app.reporting.exporter import ReportExporter
from backend.app.services.build_service import BuildService
from backend.app.services.causality_graph_service import CausalityGraphService
from backend.app.services.overview_service import OverviewService
from backend.app.services.passport_service import PassportService
from backend.app.services.perturbation_service import PerturbationService
from backend.app.services.remediation_engine import (
    ApprovalRequiredError,
    DiagnosisOnlyPolicyError,
    RemediationEngine,
)
from backend.app.services.repository_service import RepositoryService


class ScanFolderRequest(BaseModel):
    folder_path: str


class PerturbationRunRequest(BaseModel):
    commit_ref: str = "HEAD"
    param_names: Optional[list[str]] = None
    execute_real_builder: bool = False


class RemediationApproveRequest(BaseModel):
    approved: bool = False
    repository_id: Optional[str] = None


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    await init_db()
    yield


def create_app() -> FastAPI:
    """Thin optional HTTP adapter delegating strictly to ReproForge application services."""
    api = FastAPI(
        title="ReproForge Headless Automation API",
        version=settings.APP_VERSION,
        description="Optional HTTP/REST adapter for automation & CI pipelines. Desktop shell uses stdio JSON-RPC.",
        lifespan=_lifespan,
    )

    @api.get("/health")
    async def health() -> Dict[str, str]:
        return {"status": "ok", "service": "reproforge-headless-api", "version": settings.APP_VERSION}

    @api.get("/api/overview")
    async def get_overview() -> Dict[str, Any]:
        return await OverviewService().get_overview_metrics()

    @api.get("/api/repositories")
    async def list_repositories() -> Dict[str, Any]:
        repos = await RepositoryService().list_repositories()
        return {
            "count": len(repos),
            "repositories": [
                {
                    "id": r.id,
                    "name": r.name,
                    "repo_url": r.repo_url,
                    "repo_type": r.repo_type,
                    "default_branch": r.default_branch,
                    "github_slug": r.github_slug,
                    "upstream_github_url": r.upstream_github_url,
                }
                for r in repos
            ],
        }

    @api.post("/api/repositories/scan-folder")
    async def scan_folder(req: ScanFolderRequest) -> Dict[str, Any]:
        return await RepositoryService().discover_folder_candidates(Path(req.folder_path))

    @api.get("/api/builds")
    async def list_builds(limit: int = Query(default=50, ge=1, le=500)) -> Dict[str, Any]:
        builds = await BuildService().list_builds(limit=limit)
        return {
            "count": len(builds),
            "builds": [
                {
                    "id": b.id,
                    "repository_id": b.repository_id,
                    "revision_id": b.revision_id,
                    "profile_name": b.profile_name,
                    "status": b.status,
                    "exit_code": b.exit_code,
                }
                for b in builds
            ],
        }

    @api.get("/api/causality/latest")
    async def get_latest_causality() -> Dict[str, Any]:
        graph = await CausalityGraphService().get_latest_graph()
        return graph or {"nodes": [], "edges": []}

    @api.get("/api/passports/{repository_id}")
    async def get_passport(repository_id: str) -> Dict[str, Any]:
        try:
            return await PassportService().generate_passport(repository_id=repository_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @api.post("/api/perturbation/{repository_id}")
    async def run_perturbation(repository_id: str, req: PerturbationRunRequest) -> Dict[str, Any]:
        try:
            return await PerturbationService().run_campaign(
                repository_id=repository_id,
                commit_ref=req.commit_ref,
                param_names=req.param_names,
                execute_real_builder=req.execute_real_builder,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @api.get("/api/remediation")
    async def list_remediation(repository_id: Optional[str] = None) -> Dict[str, Any]:
        proposals = await RemediationEngine().list_proposals(repository_id=repository_id)
        return {"count": len(proposals), "proposals": proposals}

    @api.post("/api/remediation/{proposal_id}/approve")
    async def approve_remediation(proposal_id: str, req: RemediationApproveRequest) -> Dict[str, Any]:
        try:
            return await RemediationEngine().approve_and_verify(
                proposal_id=proposal_id,
                repository_id=req.repository_id,
                approved=req.approved,
            )
        except (ApprovalRequiredError, DiagnosisOnlyPolicyError) as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @api.get("/api/reports/comparison/{repository_id}")
    async def export_passport_or_report(
        repository_id: str,
        format: str = Query(default="json", pattern="^(json|csv|html)$"),
    ):
        try:
            passport = await PassportService().generate_passport(repository_id=repository_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        details = {
            "id": passport["comparison_cri"].get("comparison_id") or repository_id,
            "build_a_id": passport["builds"][0]["id"] if passport["builds"] else "—",
            "build_b_id": passport["builds"][1]["id"] if len(passport["builds"]) > 1 else "—",
            "verdict": passport["comparison_cri"]["verdict"],
            "score": passport["comparison_cri"],
            "diff_entries": [],
            "diagnostic_findings": passport["epistemic_audit"]["findings"],
        }
        if format == "html":
            return HTMLResponse(ReportExporter.comparison_to_html(details))
        if format == "csv":
            return PlainTextResponse(ReportExporter.comparison_to_csv(details), media_type="text/csv")
        return passport

    return api


app = create_app()
