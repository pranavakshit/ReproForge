from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings
from backend.app.database.session import AsyncSessionLocal
from backend.app.models.entities import (
    ArtifactItemModel,
    BuildComparisonModel,
    BuildExecutionModel,
    DiagnosticFindingModel,
    RepositoryModel,
    ReproducibilityScoreModel,
    SourceRevisionModel,
)
from backend.app.services.perturbation_service import PerturbationService

_SECRET_URL_CREDS_RE = re.compile(r"(https?://)([^/@\s]+):([^/@\s]+)@")
_SECRET_TOKEN_RE = re.compile(
    r"(ghp_[A-Za-z0-9]{10,}|github_pat_[A-Za-z0-9_]{10,}|Bearer\s+[A-Za-z0-9._-]{8,})"
)
_SENSITIVE_KEY_TERMS = ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "CREDENTIAL", "AUTH")


class PassportService:
    """Generates and exports sanitized Reproducibility Passports (reproforge-passport-v1.0.json)."""

    SCHEMA_VERSION = "1.0.0"
    PASSPORT_FORMAT = "reproforge-passport-v1.0"

    def __init__(self, session: Optional[AsyncSession] = None) -> None:
        self._session = session

    @staticmethod
    def sanitize_text(text: Optional[str]) -> str:
        if not text:
            return ""
        cleaned = _SECRET_URL_CREDS_RE.sub(r"\1[REDACTED]@", text)
        cleaned = _SECRET_TOKEN_RE.sub("[REDACTED_TOKEN]", cleaned)
        return cleaned

    @classmethod
    def sanitize_env(cls, env_dict: Dict[str, Any]) -> Dict[str, str]:
        safe: Dict[str, str] = {}
        for k, v in env_dict.items():
            upper = k.upper()
            if any(term in upper for term in _SENSITIVE_KEY_TERMS):
                safe[k] = "[REDACTED]"
            else:
                safe[k] = cls.sanitize_text(str(v))
        return safe

    async def generate_passport(self, repository_id: Optional[str] = None) -> Dict[str, Any]:
        if self._session is not None:
            return await self._build_passport(self._session, repository_id)
        async with AsyncSessionLocal() as session:
            return await self._build_passport(session, repository_id)

    async def _build_passport(
        self,
        session: AsyncSession,
        repository_id: Optional[str],
    ) -> Dict[str, Any]:
        if repository_id:
            repo_stmt = select(RepositoryModel).where(RepositoryModel.id == repository_id)
        else:
            repo_stmt = select(RepositoryModel).order_by(RepositoryModel.created_at)
        repo = (await session.execute(repo_stmt)).scalars().first()
        if repo is None:
            raise ValueError("No tracked repository available for passport generation")

        builds_stmt = (
            select(BuildExecutionModel)
            .where(BuildExecutionModel.repository_id == repo.id)
            .order_by(desc(BuildExecutionModel.started_at), desc(BuildExecutionModel.id))
            .limit(10)
        )
        builds = list((await session.execute(builds_stmt)).scalars().all())
        build_ids = [b.id for b in builds]
        rev_ids = list({b.revision_id for b in builds if b.revision_id})

        commit_map: Dict[str, str] = {}
        if rev_ids:
            for r in (
                await session.execute(
                    select(SourceRevisionModel).where(SourceRevisionModel.id.in_(rev_ids))
                )
            ).scalars().all():
                commit_map[r.id] = r.commit_sha

        artifact_hashes: Dict[str, str] = {}
        if build_ids:
            art_stmt = select(ArtifactItemModel).where(
                ArtifactItemModel.build_execution_id.in_(build_ids)
            )
            for art in (await session.execute(art_stmt)).scalars().all():
                if art.build_execution_id not in artifact_hashes:
                    artifact_hashes[art.build_execution_id] = art.strict_sha256

        latest_comp: Optional[BuildComparisonModel] = None
        score: Optional[ReproducibilityScoreModel] = None
        findings: List[DiagnosticFindingModel] = []

        if build_ids:
            comp_stmt = (
                select(BuildComparisonModel)
                .where(
                    or_(
                        BuildComparisonModel.build_id_1.in_(build_ids),
                        BuildComparisonModel.build_id_2.in_(build_ids),
                    )
                )
                .order_by(desc(BuildComparisonModel.created_at))
                .limit(1)
            )
            latest_comp = (await session.execute(comp_stmt)).scalars().first()

        if latest_comp is not None:
            score_stmt = select(ReproducibilityScoreModel).where(
                ReproducibilityScoreModel.comparison_id == latest_comp.id
            )
            score = (await session.execute(score_stmt)).scalar_one_or_none()
            find_stmt = select(DiagnosticFindingModel).where(
                DiagnosticFindingModel.comparison_id == latest_comp.id
            )
            findings = list((await session.execute(find_stmt)).scalars().all())

        verdict_raw = (score.verdict if score else "") or (
            "bit_for_bit_reproducible"
            if latest_comp and latest_comp.exact_match
            else ("normalized_reproducible" if latest_comp and latest_comp.normalized_match else ("non_reproducible" if latest_comp else "unverified"))
        )
        verdict = verdict_raw.upper()
        if verdict in {"BIT_FOR_BIT", "BIT_FOR_BIT_REPRODUCIBLE", "REPRODUCIBLE"}:
            verification_status = "VERIFIED_BIT_FOR_BIT"
        elif verdict in {"NORMALIZED", "NORMALIZED_REPRODUCIBLE", "EQUIVALENT"}:
            verification_status = "VERIFIED_NORMALIZED"
        elif verdict in {"DRIFT", "DIVERGENT", "NON_REPRODUCIBLE", "PARTIALLY_REPRODUCIBLE"}:
            verification_status = "DRIFT_DETECTED"
        else:
            verification_status = "UNVERIFIED"

        epistemic_counts: Dict[str, int] = {
            "OBSERVED": 0,
            "INFERRED": 0,
            "HYPOTHESIS": 0,
            "EXPERIMENTALLY_VALIDATED": 0,
        }
        findings_out: List[Dict[str, Any]] = []
        for f in findings:
            trail_str = str(f.evidence_trail or []).lower()
            if "experimentally_validated" in trail_str:
                ep = "EXPERIMENTALLY_VALIDATED"
            elif "observed" in trail_str:
                ep = "OBSERVED"
            elif (f.confidence or "").lower() == "high":
                ep = "INFERRED"
            else:
                ep = "HYPOTHESIS"
            epistemic_counts[ep] = epistemic_counts.get(ep, 0) + 1
            findings_out.append(
                {
                    "id": f.id,
                    "category": f.category,
                    "confidence": (f.confidence or "medium").upper(),
                    "epistemic_status": ep,
                    "summary": self.sanitize_text(f.root_cause_summary),
                    "suggested_remediation": self.sanitize_text(f.suggested_remediation),
                }
            )

        campaigns = PerturbationService.load_campaigns(repository_id=repo.id)

        payload: Dict[str, Any] = {
            "schema_version": self.SCHEMA_VERSION,
            "passport_format": self.PASSPORT_FORMAT,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "verification_status": verification_status,
            "repository": {
                "id": repo.id,
                "name": repo.name,
                "repo_url": self.sanitize_text(repo.repo_url),
                "upstream_github_url": self.sanitize_text(repo.upstream_github_url),
                "github_slug": repo.github_slug,
                "default_branch": repo.default_branch,
            },
            "builds": [
                {
                    "id": b.id,
                    "commit_sha": commit_map.get(b.revision_id, b.revision_id or "HEAD"),
                    "backend": b.profile_name,
                    "status": b.status,
                    "primary_sha256": artifact_hashes.get(b.id),
                }
                for b in builds
            ],
            "comparison_cri": {
                "comparison_id": latest_comp.id if latest_comp else None,
                "verdict": verdict,
                "content_agreement_ratio": round(score.content_agreement_ratio, 4) if score else 0.0,
                "metadata_agreement_ratio": round(score.metadata_agreement_ratio, 4) if score else 0.0,
                "environment_consistency_score": round(score.environment_consistency_score, 4) if score else 0.0,
                "composite_reproducibility_index": round(score.composite_reproducibility_index, 4) if score else 0.0,
            },
            "epistemic_audit": {
                "counts": epistemic_counts,
                "findings": findings_out,
            },
            "perturbation_campaigns": campaigns[:5],
        }

        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        payload["passport_digest"] = f"sha256:{hashlib.sha256(canonical).hexdigest()}"
        return payload

    async def export_passport(
        self,
        repository_id: Optional[str] = None,
        fmt: str = "json",
    ) -> Dict[str, Any]:
        passport = await self.generate_passport(repository_id=repository_id)
        repo_slug = re.sub(r"[^A-Za-z0-9._-]+", "_", passport["repository"]["name"])
        out_dir = Path(settings.ARTIFACTS_DIR) / "passports"
        out_dir.mkdir(parents=True, exist_ok=True)

        if fmt.lower() in {"markdown", "md"}:
            content = self.render_markdown(passport)
            out_path = out_dir / f"{repo_slug}.passport.md"
        else:
            content = json.dumps(passport, indent=2)
            out_path = out_dir / f"{repo_slug}.passport.v1.json"

        out_path.write_text(content, encoding="utf-8")
        return {
            "repository_id": passport["repository"]["id"],
            "repository_name": passport["repository"]["name"],
            "verification_status": passport["verification_status"],
            "passport_digest": passport["passport_digest"],
            "file_path": str(out_path),
            "content": content,
        }

    @staticmethod
    def render_markdown(passport: Dict[str, Any]) -> str:
        repo = passport["repository"]
        cri = passport["comparison_cri"]
        counts = passport["epistemic_audit"]["counts"]
        lines = [
            f"# ReproForge Reproducibility Passport — {repo['name']}",
            "",
            f"- **Format:** `{passport['passport_format']}` (v{passport['schema_version']})",
            f"- **Verification Status:** `{passport['verification_status']}`",
            f"- **Generated At:** `{passport['generated_at']}`",
            f"- **Canonical Digest:** `{passport['passport_digest']}`",
            f"- **GitHub Upstream:** `{repo.get('github_slug') or 'Local Only'}`",
            "",
            "## Reproducibility Metrics & CRI Decomposition",
            f"- **Verdict:** `{cri['verdict']}`",
            f"- **Content Agreement:** `{cri['content_agreement_ratio']:.2%}`",
            f"- **Metadata Agreement:** `{cri['metadata_agreement_ratio']:.2%}`",
            f"- **Environment Consistency:** `{cri['environment_consistency_score']:.2%}`",
            f"- **Composite Index (CRI):** `{cri['composite_reproducibility_index']:.3f}`",
            "",
            "## Epistemic Audit Breakdown",
            f"- `EXPERIMENTALLY_VALIDATED`: {counts.get('EXPERIMENTALLY_VALIDATED', 0)}",
            f"- `OBSERVED`: {counts.get('OBSERVED', 0)}",
            f"- `INFERRED`: {counts.get('INFERRED', 0)}",
            f"- `HYPOTHESIS`: {counts.get('HYPOTHESIS', 0)}",
            "",
            "## Tracked Builds",
            "| Build ID | Commit | Backend | Status | Primary SHA-256 |",
            "| :--- | :--- | :--- | :--- | :--- |",
        ]
        for b in passport["builds"]:
            sha_disp = (b["primary_sha256"] or "—")[:18]
            lines.append(
                f"| `{b['id']}` | `{str(b['commit_sha'])[:8]}` | {b['backend']} | {b['status']} | `{sha_disp}` |"
            )
        return "\n".join(lines) + "\n"
