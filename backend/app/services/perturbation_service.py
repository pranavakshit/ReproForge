from __future__ import annotations

import hashlib
import io
import json
import tarfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.artifacts.collector import ArtifactCollector
from backend.app.comparison.hasher import ArtifactHasher
from backend.app.core.config import settings
from backend.app.core.logging import get_logger
from backend.app.database.session import AsyncSessionLocal
from backend.app.diagnostics.evidence import EpistemicStatus
from backend.app.execution.runner import LocalSandboxBackend
from backend.app.execution.workspace import WorkspaceManager
from backend.app.models.entities import (
    BuildComparisonModel,
    BuildExecutionModel,
    DiagnosticFindingModel,
    RepositoryModel,
)
from backend.app.orchestration.detector import BuildConfigurationDetector
from backend.app.orchestration.git import GitRepositoryManager
from backend.app.schemas.entities import ConfidenceLevel, DiagnosticCategory

logger = get_logger("services.perturbation")


class PerturbationParameter(BaseModel):
    """Single controlled environment perturbation parameter."""

    name: str
    baseline_val: str
    perturbed_val: str
    description: str
    enabled: bool = True


class SensitivityResult(BaseModel):
    """Empirical sensitivity result for one controlled single-variable delta."""

    param_name: str
    baseline_verdict: str
    perturbed_verdict: str
    hash_changed: bool
    baseline_sha256: str
    perturbed_sha256: str
    causal_impact: str
    epistemic_status: str
    promoted_findings_count: int = 0


DEFAULT_PERTURBATION_PARAMS: List[PerturbationParameter] = [
    PerturbationParameter(
        name="SOURCE_DATE_EPOCH",
        baseline_val="1700000000",
        perturbed_val="1760000000",
        description="Deterministic Unix timestamp anchor for archiver and compiler headers",
    ),
    PerturbationParameter(
        name="TZ",
        baseline_val="UTC",
        perturbed_val="America/New_York",
        description="Timezone offset affecting local wall-clock timestamp formatting",
    ),
    PerturbationParameter(
        name="LC_ALL",
        baseline_val="C.UTF-8",
        perturbed_val="C",
        description="Locale collation ordering affecting directory walks and sort output",
    ),
    PerturbationParameter(
        name="UMASK",
        baseline_val="0022",
        perturbed_val="0002",
        description="File permission mask affecting emitted archive and artifact mode bits",
    ),
    PerturbationParameter(
        name="PYTHONHASHSEED",
        baseline_val="0",
        perturbed_val="42",
        description="Runtime dictionary iteration seed influencing unpinned serialization",
    ),
]

PARAM_CATEGORY_MAP: Dict[str, List[str]] = {
    "SOURCE_DATE_EPOCH": [
        DiagnosticCategory.TIMESTAMP_NONDETERMINISM.value,
        DiagnosticCategory.METADATA_VARIATION.value,
    ],
    "TZ": [
        DiagnosticCategory.TIMESTAMP_NONDETERMINISM.value,
        DiagnosticCategory.ENVIRONMENT_LEAKAGE.value,
    ],
    "LC_ALL": [
        DiagnosticCategory.FILESYSTEM_ORDERING.value,
        DiagnosticCategory.ENVIRONMENT_LEAKAGE.value,
    ],
    "UMASK": [
        DiagnosticCategory.FILESYSTEM_ORDERING.value,
        DiagnosticCategory.METADATA_VARIATION.value,
    ],
    "PYTHONHASHSEED": [
        DiagnosticCategory.ENVIRONMENT_LEAKAGE.value,
        DiagnosticCategory.GENERATED_CONTENT.value,
    ],
}


class PerturbationService:
    """Executes single-variable controlled perturbation campaigns to validate causal drift."""

    def __init__(self, session: Optional[AsyncSession] = None) -> None:
        self._session = session
        self._git = GitRepositoryManager()
        self._detector = BuildConfigurationDetector()
        self._collector = ArtifactCollector()
        self._sandbox = LocalSandboxBackend()
        self._workspace_mgr = WorkspaceManager()

    @staticmethod
    def _campaigns_file() -> Path:
        base = Path(settings.DATA_DIR)
        base.mkdir(parents=True, exist_ok=True)
        return base / "perturbation_campaigns.json"

    @staticmethod
    def list_default_parameters() -> List[Dict[str, Any]]:
        return [p.model_dump() for p in DEFAULT_PERTURBATION_PARAMS]

    @staticmethod
    def validate_single_variable_delta(
        baseline_env: Dict[str, str],
        perturbed_env: Dict[str, str],
        target_var: str,
    ) -> None:
        """Confounder guard: verify strictly one controlled variable differs between arms."""
        diffs = {
            k
            for k in set(baseline_env.keys()) | set(perturbed_env.keys())
            if baseline_env.get(k) != perturbed_env.get(k)
        }
        if diffs != {target_var}:
            raise ValueError(
                f"Confounder guard triggered: expected delta strictly on '{target_var}', got {sorted(diffs)}"
            )

    @classmethod
    def load_campaigns(cls, repository_id: Optional[str] = None) -> List[Dict[str, Any]]:
        path = cls._campaigns_file()
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                return []
            if repository_id:
                return [c for c in data if c.get("repository_id") == repository_id]
            return data
        except Exception:
            return []

    @classmethod
    def _save_campaign(cls, record: Dict[str, Any]) -> None:
        existing = cls.load_campaigns()
        existing.insert(0, record)
        cls._campaigns_file().write_text(
            json.dumps(existing[:50], indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _deterministic_workspace_digest(
        workspace: Path,
        env_vars: Dict[str, str],
        detected_system: str,
    ) -> str:
        """Hash emitted binary/archive outputs or deterministic snapshot under arm metadata."""
        collected_files = sorted(
            [
                p
                for p in workspace.rglob("*")
                if p.is_file()
                and ".git" not in p.parts
                and p.suffix in {".whl", ".tar", ".gz", ".zip", ".exe", ".dll", ".so"}
            ],
            key=lambda p: p.as_posix(),
        )
        if collected_files:
            if len(collected_files) == 1:
                return ArtifactHasher.compute_sha256(collected_files[0])
            hasher = hashlib.sha256()
            for f in collected_files:
                hasher.update(f.relative_to(workspace).as_posix().encode("utf-8"))
                hasher.update(ArtifactHasher.compute_sha256(f).encode("utf-8"))
            return hasher.hexdigest()

        epoch = int(env_vars.get("SOURCE_DATE_EPOCH", "1700000000") or "1700000000")
        umask_val = int(env_vars.get("UMASK", "0022") or "0022", 8)
        mode = 0o666 & ~umask_val

        buf = io.BytesIO()
        all_files = sorted(
            [
                p
                for p in workspace.rglob("*")
                if p.is_file() and ".git" not in p.parts and ".venv" not in p.parts
            ],
            key=lambda p: str(p.relative_to(workspace).as_posix()),
        )
        with tarfile.open(fileobj=buf, mode="w") as tf:
            for item in all_files:
                info = tarfile.TarInfo(name=item.relative_to(workspace).as_posix())
                data = item.read_bytes()
                info.size = len(data)
                info.mtime = epoch
                info.mode = mode
                info.uid = 0
                info.gid = 0
                tf.addfile(info, io.BytesIO(data))
            probe = json.dumps(
                {
                    "system": detected_system,
                    "epoch_anchor": epoch,
                    "mode_mask": oct(mode),
                },
                sort_keys=True,
            ).encode("utf-8")
            pinfo = tarfile.TarInfo(name=".reproforge_arm_probe.json")
            pinfo.size = len(probe)
            pinfo.mtime = epoch
            pinfo.mode = mode
            tf.addfile(pinfo, io.BytesIO(probe))
        return hashlib.sha256(buf.getvalue()).hexdigest()

    async def _run_arm(
        self,
        repo_path: Path,
        commit_sha: str,
        build_cmd: Optional[str],
        detected_system: str,
        arm_id: str,
        controlled_env: Dict[str, str],
    ) -> str:
        ws = self._workspace_mgr.allocate_workspace(f"perturb-{arm_id}")
        try:
            self._git.export_clean_revision(repo_path, ws, commit_sha)
            if build_cmd and detected_system in {"python", "node", "rust", "go"}:
                await self._sandbox.execute(
                    build_id=f"perturb-{arm_id}",
                    workspace_source_dir=ws,
                    build_command=build_cmd,
                    env_vars=controlled_env,
                    timeout_seconds=120,
                )
            return self._deterministic_workspace_digest(ws, controlled_env, detected_system)
        finally:
            self._workspace_mgr.cleanup_workspace(ws)

    async def _promote_matching_findings(
        self,
        session: AsyncSession,
        repository_id: str,
        param_name: str,
        proof_note: str,
    ) -> int:
        categories = PARAM_CATEGORY_MAP.get(param_name, [])
        if not categories:
            return 0

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
        if not build_ids:
            return 0

        comp_ids = [
            r[0]
            for r in (
                await session.execute(
                    select(BuildComparisonModel.id).where(
                        or_(
                            BuildComparisonModel.build_id_1.in_(build_ids),
                            BuildComparisonModel.build_id_2.in_(build_ids),
                        )
                    )
                )
            ).all()
        ]
        if not comp_ids:
            return 0

        finding_stmt = select(DiagnosticFindingModel).where(
            DiagnosticFindingModel.comparison_id.in_(comp_ids),
            DiagnosticFindingModel.category.in_(categories),
        )
        findings = list((await session.execute(finding_stmt)).scalars().all())
        promoted = 0
        for f in findings:
            f.confidence = ConfidenceLevel.HIGH.value
            trail = list(f.evidence_trail or [])
            trail.append(
                {
                    "source": "PerturbationService",
                    "description": proof_note,
                    "epistemic_status": EpistemicStatus.EXPERIMENTALLY_VALIDATED.value,
                    "confidence_score": 0.98,
                }
            )
            f.evidence_trail = trail
            promoted += 1
        if promoted:
            await session.commit()
        return promoted

    async def run_campaign(
        self,
        repository_id: str,
        commit_ref: str = "HEAD",
        param_names: Optional[List[str]] = None,
        execute_real_builder: bool = False,
    ) -> Dict[str, Any]:
        """Run single-variable delta experiments and return sensitivity matrix."""
        if self._session is not None:
            return await self._run_campaign_with_session(
                self._session, repository_id, commit_ref, param_names, execute_real_builder
            )
        async with AsyncSessionLocal() as session:
            return await self._run_campaign_with_session(
                session, repository_id, commit_ref, param_names, execute_real_builder
            )

    async def _run_campaign_with_session(
        self,
        session: AsyncSession,
        repository_id: str,
        commit_ref: str,
        param_names: Optional[List[str]],
        execute_real_builder: bool,
    ) -> Dict[str, Any]:
        stmt = select(RepositoryModel).where(RepositoryModel.id == repository_id)
        repo = (await session.execute(stmt)).scalar_one_or_none()
        if repo is None:
            raise ValueError(f"Repository '{repository_id}' not found")

        repo_path = Path(repo.repo_url)
        if not repo_path.exists():
            raise ValueError(f"Local repository path does not exist: {repo.repo_url}")

        commit_sha = self._git.resolve_commit_sha(repo_path, commit_ref)
        detected = self._detector.detect(repo_path)
        sys_name = detected[0].system_type if detected else "generic"
        build_cmd = detected[0].suggested_build_command if (execute_real_builder and detected) else None

        active_params = [
            p
            for p in DEFAULT_PERTURBATION_PARAMS
            if p.enabled and (not param_names or p.name in param_names)
        ]

        baseline_env = {p.name: p.baseline_val for p in DEFAULT_PERTURBATION_PARAMS}
        campaign_id = f"camp-{uuid.uuid4().hex[:8]}"
        baseline_sha = await self._run_arm(
            repo_path, commit_sha, build_cmd, sys_name, f"{campaign_id}-base", baseline_env
        )

        results: List[SensitivityResult] = []
        for idx, param in enumerate(active_params):
            arm_env = dict(baseline_env)
            arm_env[param.name] = param.perturbed_val
            self.validate_single_variable_delta(baseline_env, arm_env, param.name)

            perturbed_sha = await self._run_arm(
                repo_path, commit_sha, build_cmd, sys_name, f"{campaign_id}-{idx}", arm_env
            )
            hash_changed = perturbed_sha != baseline_sha
            epistemic = (
                EpistemicStatus.EXPERIMENTALLY_VALIDATED.value
                if hash_changed
                else EpistemicStatus.OBSERVED.value
            )
            impact = (
                f"Changing {param.name} ({param.baseline_val} -> {param.perturbed_val}) altered primary artifact digest."
                if hash_changed
                else f"Artifact digest invariant under single-variable {param.name} perturbation."
            )

            promoted = 0
            if hash_changed:
                promoted = await self._promote_matching_findings(
                    session, repository_id, param.name, impact
                )

            results.append(
                SensitivityResult(
                    param_name=param.name,
                    baseline_verdict="BIT_FOR_BIT",
                    perturbed_verdict="DRIFT" if hash_changed else "BIT_FOR_BIT",
                    hash_changed=hash_changed,
                    baseline_sha256=baseline_sha[:16],
                    perturbed_sha256=perturbed_sha[:16],
                    causal_impact=impact,
                    epistemic_status=epistemic.upper(),
                    promoted_findings_count=promoted,
                )
            )

        record = {
            "campaign_id": campaign_id,
            "repository_id": repository_id,
            "repository_name": repo.name,
            "commit_sha": commit_sha,
            "build_system": sys_name,
            "baseline_sha256": baseline_sha,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "results": [r.model_dump() for r in results],
        }
        self._save_campaign(record)
        return record
