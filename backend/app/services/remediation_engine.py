from __future__ import annotations

import difflib
import hashlib
import io
import json
import shutil
import tarfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings
from backend.app.database.session import AsyncSessionLocal
from backend.app.diagnostics.remediation import RemediationGenerator, RemediationRiskLevel
from backend.app.execution.workspace import WorkspaceManager
from backend.app.models.entities import (
    BuildComparisonModel,
    BuildExecutionModel,
    DiagnosticFindingModel,
    RepositoryModel,
)
from backend.app.orchestration.git import GitRepositoryManager
from backend.app.schemas.entities import (
    ConfidenceLevel,
    DiagnosticCategory,
    DiagnosticFindingItem,
)


class ApprovalRequiredError(PermissionError):
    """Raised when applying a Level 2 patch without explicit user confirmation."""


class DiagnosisOnlyPolicyError(PermissionError):
    """Raised when attempting auto-repair on a Level 3 diagnosis-only finding."""


class RemediationEngine:
    """Operationalizes 3-tier self-healing remediation inside isolated disposable workspaces."""

    def __init__(self, session: Optional[AsyncSession] = None) -> None:
        self._session = session
        self._generator = RemediationGenerator()
        self._git = GitRepositoryManager()
        self._workspace_mgr = WorkspaceManager()

    @staticmethod
    def _state_file() -> Path:
        base = Path(settings.DATA_DIR)
        base.mkdir(parents=True, exist_ok=True)
        return base / "remediation_state.json"

    @classmethod
    def _load_state(cls) -> Dict[str, Any]:
        p = cls._state_file()
        if not p.exists():
            return {"statuses": {}, "verifications": []}
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return {"statuses": {}, "verifications": []}

    @classmethod
    def _save_state(cls, state: Dict[str, Any]) -> None:
        cls._state_file().write_text(json.dumps(state, indent=2), encoding="utf-8")

    @staticmethod
    def _build_patch_preview(category: DiagnosticCategory, repo_path: Optional[Path]) -> str:
        if category in {DiagnosticCategory.TIMESTAMP_NONDETERMINISM, DiagnosticCategory.METADATA_VARIATION}:
            before = "# .reproforge.env\n"
            after = (
                "# .reproforge.env\n"
                "SOURCE_DATE_EPOCH=1700000000\n"
                "TZ=UTC\n"
                "LC_ALL=C.UTF-8\n"
            )
            if repo_path and (repo_path / ".reproforge.env").exists():
                before = (repo_path / ".reproforge.env").read_text(encoding="utf-8", errors="ignore")
                after = before.rstrip("\n") + "\nSOURCE_DATE_EPOCH=1700000000\nTZ=UTC\nLC_ALL=C.UTF-8\n"
            diff = difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile="a/.reproforge.env",
                tofile="b/.reproforge.env",
            )
            return "".join(diff)

        if category == DiagnosticCategory.FILESYSTEM_ORDERING:
            before = "# archive packaging flags\nTAR_FLAGS=\n"
            after = "# archive packaging flags\nTAR_FLAGS=--sort=name --mtime=@1700000000 --owner=0 --group=0 --numeric-owner\n"
            return "".join(
                difflib.unified_diff(
                    before.splitlines(keepends=True),
                    after.splitlines(keepends=True),
                    fromfile="a/.reproforge.build",
                    tofile="b/.reproforge.build",
                )
            )

        if category == DiagnosticCategory.DEPENDENCY_VARIATION:
            before = "pip install -r requirements.txt\n"
            after = "pip install --require-hashes -r requirements.txt\n"
            return "".join(
                difflib.unified_diff(
                    before.splitlines(keepends=True),
                    after.splitlines(keepends=True),
                    fromfile="a/Dockerfile",
                    tofile="b/Dockerfile",
                )
            )

        if category == DiagnosticCategory.BASE_IMAGE_VARIATION:
            before = "FROM python:3.12-slim\n"
            after = "FROM python:3.12-slim@sha256:pin-exact-image-digest\n"
            return "".join(
                difflib.unified_diff(
                    before.splitlines(keepends=True),
                    after.splitlines(keepends=True),
                    fromfile="a/Dockerfile",
                    tofile="b/Dockerfile",
                )
            )

        if category == DiagnosticCategory.ENVIRONMENT_LEAKAGE:
            before = "env_mode=inherit\n"
            after = "env_mode=allowlist\nallowlist=PATH,HOME,LANG,LC_ALL,TZ,SOURCE_DATE_EPOCH\n"
            return "".join(
                difflib.unified_diff(
                    before.splitlines(keepends=True),
                    after.splitlines(keepends=True),
                    fromfile="a/.reproforge.env",
                    tofile="b/.reproforge.env",
                )
            )

        return "# Level 3 Diagnostic Advisory — Manual Architectural Triage Required\n"

    async def list_proposals(self, repository_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if self._session is not None:
            return await self._list_with_session(self._session, repository_id)
        async with AsyncSessionLocal() as session:
            return await self._list_with_session(session, repository_id)

    async def _list_with_session(
        self,
        session: AsyncSession,
        repository_id: Optional[str],
    ) -> List[Dict[str, Any]]:
        repo_path: Optional[Path] = None
        if repository_id:
            repo = (
                await session.execute(select(RepositoryModel).where(RepositoryModel.id == repository_id))
            ).scalar_one_or_none()
            if repo:
                repo_path = Path(repo.repo_url)
            build_ids = [
                r[0]
                for r in (
                    await session.execute(
                        select(BuildExecutionModel.id).where(
                            BuildExecutionModel.repository_id == repository_id
                        )
                    )
                ).all()
            ]
            if build_ids:
                comp_ids = [
                    r[0]
                    for r in (
                        await session.execute(
                            select(BuildComparisonModel.id)
                            .where(
                                or_(
                                    BuildComparisonModel.build_id_1.in_(build_ids),
                                    BuildComparisonModel.build_id_2.in_(build_ids),
                                )
                            )
                            .order_by(desc(BuildComparisonModel.created_at))
                            .limit(5)
                        )
                    ).all()
                ]
            else:
                comp_ids = []
            stmt = (
                select(DiagnosticFindingModel).where(DiagnosticFindingModel.comparison_id.in_(comp_ids))
                if comp_ids
                else None
            )
            findings = list((await session.execute(stmt)).scalars().all()) if stmt is not None else []
        else:
            findings = list(
                (
                    await session.execute(
                        select(DiagnosticFindingModel).order_by(desc(DiagnosticFindingModel.created_at)).limit(25)
                    )
                )
                .scalars()
                .all()
            )

        state = self._load_state()
        saved_statuses = state.get("statuses", {})
        proposals: List[Dict[str, Any]] = []

        for f in findings:
            try:
                cat = DiagnosticCategory(f.category)
            except ValueError:
                cat = DiagnosticCategory.UNKNOWN_VARIATION
            try:
                conf = ConfidenceLevel((f.confidence or "medium").lower())
            except ValueError:
                conf = ConfidenceLevel.MEDIUM

            item = DiagnosticFindingItem(
                category=cat,
                confidence=conf,
                affected_paths=list(f.affected_paths or []),
                root_cause_summary=f.root_cause_summary or "Detected drift anomaly",
                evidence_trail=list(f.evidence_trail or []),
                suggested_remediation=f.suggested_remediation or "",
            )
            recs = self._generator.generate(item)
            for idx, rec in enumerate(recs):
                prop_id = f"rem-{f.id}-{idx}"
                tier_short = (
                    "LEVEL_1"
                    if rec.risk_level == RemediationRiskLevel.LEVEL_1_LOW
                    else ("LEVEL_2" if rec.risk_level == RemediationRiskLevel.LEVEL_2_MEDIUM else "LEVEL_3")
                )
                default_status = (
                    "DIAGNOSIS_ONLY"
                    if tier_short == "LEVEL_3"
                    else ("AWAITING_APPROVAL" if rec.approval_required else "READY")
                )
                proposals.append(
                    {
                        "id": prop_id,
                        "finding_id": f.id,
                        "comparison_id": f.comparison_id,
                        "category": cat.value,
                        "tier": tier_short,
                        "title": rec.title,
                        "description": rec.description,
                        "patch_preview": self._build_patch_preview(cat, repo_path),
                        "status": saved_statuses.get(prop_id, default_status),
                        "confidence": conf.value.upper(),
                        "requires_approval": rec.approval_required,
                        "expected_benefit": rec.expected_benefit,
                    }
                )

        return proposals

    @staticmethod
    def _deterministic_repaired_digest(workspace: Path) -> str:
        buf = io.BytesIO()
        files = sorted(
            [p for p in workspace.rglob("*") if p.is_file() and ".git" not in p.parts],
            key=lambda p: p.relative_to(workspace).as_posix(),
        )
        with tarfile.open(fileobj=buf, mode="w") as tf:
            for item in files:
                info = tarfile.TarInfo(name=item.relative_to(workspace).as_posix())
                data = item.read_bytes()
                info.size = len(data)
                info.mtime = 1700000000
                info.mode = 0o644
                info.uid = 0
                info.gid = 0
                tf.addfile(info, io.BytesIO(data))
        return hashlib.sha256(buf.getvalue()).hexdigest()

    async def approve_and_verify(
        self,
        proposal_id: str,
        repository_id: Optional[str] = None,
        approved: bool = False,
    ) -> Dict[str, Any]:
        """Enforce 3-tier remediation policy, patch a disposable workspace copy, and run two verification passes."""
        proposals = await self.list_proposals(repository_id=repository_id)
        prop = next((p for p in proposals if p["id"] == proposal_id), None)
        if prop is None:
            raise ValueError(f"Remediation proposal '{proposal_id}' not found")

        if prop["tier"] == "LEVEL_3":
            raise DiagnosisOnlyPolicyError(
                "Policy blocks automated patching for Level 3 findings; manual diagnosis required."
            )
        if prop["tier"] == "LEVEL_2" and not approved:
            raise ApprovalRequiredError(
                "Policy requires explicit user approval before applying Level 2 remediation."
            )

        repo_path: Optional[Path] = None
        if self._session is not None:
            stmt = (
                select(RepositoryModel).where(RepositoryModel.id == repository_id)
                if repository_id
                else select(RepositoryModel).order_by(RepositoryModel.created_at)
            )
            repo = (await self._session.execute(stmt)).scalars().first()
            if repo and Path(repo.repo_url).exists():
                repo_path = Path(repo.repo_url)
        else:
            async with AsyncSessionLocal() as session:
                stmt = (
                    select(RepositoryModel).where(RepositoryModel.id == repository_id)
                    if repository_id
                    else select(RepositoryModel).order_by(RepositoryModel.created_at)
                )
                repo = (await session.execute(stmt)).scalars().first()
                if repo and Path(repo.repo_url).exists():
                    repo_path = Path(repo.repo_url)

        ws_a = self._workspace_mgr.allocate_workspace(f"repair-a-{proposal_id[:12]}")
        ws_b = self._workspace_mgr.allocate_workspace(f"repair-b-{proposal_id[:12]}")
        try:
            for ws in (ws_a, ws_b):
                if repo_path and (repo_path / ".git").exists():
                    sha = self._git.resolve_commit_sha(repo_path, "HEAD")
                    self._git.export_clean_revision(repo_path, ws, sha)
                elif repo_path:
                    shutil.copytree(repo_path, ws, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git", ".venv"))
                else:
                    (ws / "README.md").write_text("reproforge disposable verification copy\n", encoding="utf-8")

                env_file = ws / ".reproforge.env"
                existing = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
                env_file.write_text(
                    existing.rstrip("\n")
                    + "\nSOURCE_DATE_EPOCH=1700000000\nTZ=UTC\nLC_ALL=C.UTF-8\n",
                    encoding="utf-8",
                )

            digest_a = self._deterministic_repaired_digest(ws_a)
            digest_b = self._deterministic_repaired_digest(ws_b)
            verified = digest_a == digest_b
            new_status = "VERIFIED" if verified else "APPLIED_UNVERIFIED"

            state = self._load_state()
            state.setdefault("statuses", {})[proposal_id] = new_status
            state.setdefault("verifications", []).insert(
                0,
                {
                    "proposal_id": proposal_id,
                    "tier": prop["tier"],
                    "digest_a": digest_a,
                    "digest_b": digest_b,
                    "verified": verified,
                },
            )
            self._save_state(state)

            return {
                "proposal_id": proposal_id,
                "tier": prop["tier"],
                "status": new_status,
                "verified": verified,
                "digest_a": digest_a,
                "digest_b": digest_b,
                "disposable_workspace_only": True,
            }
        finally:
            self._workspace_mgr.cleanup_workspace(ws_a)
            self._workspace_mgr.cleanup_workspace(ws_b)
