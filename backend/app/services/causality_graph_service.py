"""Service for generating, querying, and exporting the Build Drift Causality Graph."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.database.session import AsyncSessionLocal
from backend.app.models.entities import (
    BuildComparisonModel,
    BuildExecutionModel,
    DiagnosticFindingModel,
    DiffFindingModel,
    EnvironmentFingerprintModel,
    ReproducibilityScoreModel,
    SourceRevisionModel,
)


@dataclass
class CausalityNode:
    """A single entity in the Build Drift Causality Graph."""

    id: str
    label: str
    node_type: str  # 'build', 'revision', 'fingerprint', 'diff', 'rule', 'remediation', 'comparison'
    status: str = "active"
    tier: int = 4
    subtitle: str = ""
    details_summary: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CausalityEdge:
    """A directed evidence-backed relationship between two causality nodes."""

    source: str
    target: str
    relation: str  # 'TRIGGERED', 'HOSTED_IN', 'EVALUATED_BUILD', 'PRODUCED_DIFF', 'EVIDENCED_BY', 'EXPLAINED_BY', 'REMEDIATED_BY'
    epistemic_status: str  # 'OBSERVED', 'INFERRED', 'HYPOTHESIS', 'EXPERIMENTALLY_VALIDATED'
    confidence: str = "HIGH"
    details: str = ""


@dataclass
class CausalityGraph:
    """Complete graph representation with versioning and evidence provenance."""

    graph_id: str
    comparison_id: str
    created_at: str
    nodes: List[CausalityNode] = field(default_factory=list)
    edges: List[CausalityEdge] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        node_labels = {n.id: n.label.replace("\n", " • ") for n in self.nodes}
        edges_out = []
        for e in self.edges:
            edge_dict = asdict(e)
            edge_dict["source_id"] = e.source
            edge_dict["target_id"] = e.target
            edge_dict["source_label"] = node_labels.get(e.source, e.source)
            edge_dict["target_label"] = node_labels.get(e.target, e.target)
            edge_dict["evidence"] = e.details or f"{node_labels.get(e.source, e.source)} {e.relation.lower().replace('_', ' ')} {node_labels.get(e.target, e.target)}"
            edges_out.append(edge_dict)
        return {
            "graph_id": self.graph_id,
            "comparison_id": self.comparison_id,
            "created_at": self.created_at,
            "nodes": [asdict(n) for n in self.nodes],
            "edges": edges_out,
            "dot_source": self.to_dot(),
        }

    def to_dot(self) -> str:
        """Export DAG to Graphviz DOT format."""
        lines = ["digraph BuildDriftCausalityGraph {", '  rankdir="LR";', '  node [shape="box", style="rounded,filled", fontname="Helvetica"];']
        for n in self.nodes:
            color = "#E0F2FE" if n.node_type == "build" else "#DCFCE7" if n.node_type == "rule" else "#FEF3C7" if n.node_type == "diff" else "#F3E8FF"
            lines.append(f'  "{n.id}" [label="{n.label}", fillcolor="{color}"];')
        for e in self.edges:
            lines.append(f'  "{e.source}" -> "{e.target}" [label="{e.relation} [{e.epistemic_status}]"];')
        lines.append("}")
        return "\n".join(lines)


class CausalityGraphService:
    """Builds and queries causality graphs from SQLite database comparisons."""

    def __init__(self, session: Optional[AsyncSession] = None) -> None:
        self._session = session

    async def get_latest_graph(self, repository_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Construct graph for the most recent comparison run (optionally filtered to one repository)."""
        if self._session:
            return await self._build_latest_graph(self._session, repository_id=repository_id)
        async with AsyncSessionLocal() as session:
            return await self._build_latest_graph(session, repository_id=repository_id)

    async def _build_latest_graph(
        self, session: AsyncSession, repository_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        if repository_id:
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
                return None
            from sqlalchemy import or_
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
        res = await session.execute(comp_stmt)
        comp = res.scalars().first()
        if not comp:
            return None
        return await self._build_graph_for_comparison(session, comp)

    async def get_graph_for_comparison(self, comparison_id: str) -> Optional[Dict[str, Any]]:
        """Construct graph for a specific comparison run."""
        if self._session:
            return await self._query_and_build(self._session, comparison_id)
        async with AsyncSessionLocal() as session:
            return await self._query_and_build(session, comparison_id)

    async def _query_and_build(self, session: AsyncSession, comparison_id: str) -> Optional[Dict[str, Any]]:
        comp_stmt = select(BuildComparisonModel).where(BuildComparisonModel.id == comparison_id)
        res = await session.execute(comp_stmt)
        comp = res.scalars().first()
        if not comp:
            return None
        return await self._build_graph_for_comparison(session, comp)

    async def _build_graph_for_comparison(self, session: AsyncSession, comp: BuildComparisonModel) -> Dict[str, Any]:
        nodes: List[CausalityNode] = []
        edges: List[CausalityEdge] = []

        score_row = (
            await session.execute(
                select(ReproducibilityScoreModel).where(ReproducibilityScoreModel.comparison_id == comp.id)
            )
        ).scalars().first()
        verdict_txt = (score_row.verdict.upper() if score_row else ("BIT_FOR_BIT" if comp.exact_match else "DIVERGENT"))

        # 1. Comparison Root Node (Tier 4)
        comp_node_id = f"comp-{comp.id[:8]}"
        nodes.append(CausalityNode(
            id=comp_node_id,
            label=f"Comparison: {comp.build_id_1[:8]} vs {comp.build_id_2[:8]}",
            node_type="comparison",
            status="EXPERIMENTALLY_VALIDATED" if comp.exact_match else "OBSERVED",
            tier=4,
            subtitle=f"Verdict: {verdict_txt}",
            details_summary=f"Mode A Exact Match: {comp.exact_match} | Mode B Normalized Match: {comp.normalized_match} | Verdict: {verdict_txt}",
            metadata={"created_at": comp.created_at.isoformat() if comp.created_at else "", "verdict": verdict_txt},
        ))

        # 2. Build Execution Nodes (Tier 1), Revisions (Tier 0), Fingerprints (Tier 2)
        for b_id in [comp.build_id_1, comp.build_id_2]:
            b_stmt = select(BuildExecutionModel).where(BuildExecutionModel.id == b_id)
            b_res = await session.execute(b_stmt)
            build_model = b_res.scalars().first()
            if build_model:
                b_node_id = f"build-{build_model.id[:8]}"
                dur_str = f"{build_model.duration_seconds:.2f}s" if build_model.duration_seconds is not None else "n/a"
                nodes.append(CausalityNode(
                    id=b_node_id,
                    label=f"Build: {build_model.id[:12]} ({build_model.profile_name})",
                    node_type="build",
                    status="OBSERVED",
                    tier=1,
                    subtitle=f"{build_model.status} • {dur_str}",
                    details_summary=f"Build {build_model.id} ran with profile '{build_model.profile_name}', status={build_model.status}, exit={build_model.exit_code}, duration={dur_str}.",
                    metadata={
                        "status": build_model.status,
                        "duration": build_model.duration_seconds,
                        "profile": build_model.profile_name,
                        "exit_code": build_model.exit_code,
                    },
                ))
                edges.append(CausalityEdge(
                    source=b_node_id,
                    target=comp_node_id,
                    relation="EVALUATED_IN",
                    epistemic_status="OBSERVED",
                    confidence="HIGH",
                    details=f"Build {build_model.id} ({build_model.profile_name}, exit={build_model.exit_code}, {dur_str}) was evaluated in pairwise comparison {comp.id[:8]} ({verdict_txt}).",
                ))

                # Revision Node
                if build_model.revision_id:
                    rev_stmt = select(SourceRevisionModel).where(SourceRevisionModel.id == build_model.revision_id)
                    rev_res = await session.execute(rev_stmt)
                    rev = rev_res.scalars().first()
                    if rev:
                        rev_node_id = f"rev-{rev.commit_sha[:8]}"
                        if not any(n.id == rev_node_id for n in nodes):
                            nodes.append(CausalityNode(
                                id=rev_node_id,
                                label=f"Commit: {rev.commit_sha[:8]}",
                                node_type="revision",
                                status="OBSERVED",
                                tier=0,
                                subtitle=f"{rev.author_name or 'Author'} ({rev.branch_name or 'HEAD'})",
                                details_summary=f"Commit {rev.commit_sha[:12]} on branch {rev.branch_name or 'HEAD'} by {rev.author_name or 'Author'}: {(rev.commit_message or '').strip()[:120]}",
                                metadata={"message": rev.commit_message, "sha": rev.commit_sha},
                            ))
                        edges.append(CausalityEdge(
                            source=rev_node_id,
                            target=b_node_id,
                            relation="TRIGGERED",
                            epistemic_status="OBSERVED",
                            confidence="HIGH",
                            details=f"Clean git archive export of commit {rev.commit_sha[:10]} ({rev.branch_name or 'HEAD'}) triggered isolated build {build_model.id}.",
                        ))

                # Environment Fingerprint Node
                if build_model.fingerprint_id:
                    fp_stmt = select(EnvironmentFingerprintModel).where(EnvironmentFingerprintModel.id == build_model.fingerprint_id)
                    fp_res = await session.execute(fp_stmt)
                    fp = fp_res.scalars().first()
                    if fp:
                        fp_node_id = f"fp-{fp.fingerprint_hash[:8]}"
                        os_name = fp.system_info.get("os_name", "OS") if isinstance(fp.system_info, dict) else "OS"
                        cpu_arch = fp.system_info.get("cpu_arch", "") if isinstance(fp.system_info, dict) else ""
                        tz = fp.locale_info.get("timezone", "UTC") if isinstance(fp.locale_info, dict) else "UTC"
                        if not any(n.id == fp_node_id for n in nodes):
                            nodes.append(CausalityNode(
                                id=fp_node_id,
                                label=f"Env: {fp.fingerprint_hash[:8]} ({os_name})",
                                node_type="fingerprint",
                                status="OBSERVED",
                                tier=2,
                                subtitle=f"{cpu_arch} • TZ={tz}",
                                details_summary=f"Fingerprint {fp.fingerprint_hash[:16]} captured {os_name} ({cpu_arch}), timezone={tz}.",
                                metadata={"hash": fp.fingerprint_hash, "os": os_name, "tz": tz},
                            ))
                        edges.append(CausalityEdge(
                            source=fp_node_id,
                            target=b_node_id,
                            relation="HOSTED_IN",
                            epistemic_status="OBSERVED",
                            confidence="HIGH",
                            details=f"Build {build_model.id} executed under recorded environment fingerprint {fp.fingerprint_hash[:12]} ({os_name} {cpu_arch}, TZ={tz}).",
                        ))

        # 3. Diff Finding Nodes (Tier 3)
        diff_stmt = select(DiffFindingModel).where(DiffFindingModel.comparison_id == comp.id)
        diff_res = await session.execute(diff_stmt)
        diffs = list(diff_res.scalars().all())
        diff_nodes_info: List[tuple[str, DiffFindingModel]] = []
        for idx, d in enumerate(diffs[:6]):  # top 6 diffs for clarity
            diff_node_id = f"diff-{d.id[:8]}"
            det = d.details if isinstance(d.details, dict) else {}
            snippet = det.get("diff_snippet") or det.get("message") or det.get("reason") or f"{d.diff_type} difference in {d.file_path}"
            nodes.append(CausalityNode(
                id=diff_node_id,
                label=f"Diff: {d.file_path}",
                node_type="diff",
                status="OBSERVED",
                tier=3,
                subtitle=f"[{d.diff_type}]",
                details_summary=f"Artifact entry '{d.file_path}' diverged ({d.diff_type}): {snippet}",
                metadata={"diff_type": d.diff_type, "file_path": d.file_path},
            ))
            edges.append(CausalityEdge(
                source=comp_node_id,
                target=diff_node_id,
                relation="PRODUCED_DIFF",
                epistemic_status="OBSERVED",
                confidence="HIGH",
                details=f"Pairwise comparison identified {d.diff_type} discrepancy at '{d.file_path}'.",
            ))
            diff_nodes_info.append((diff_node_id, d))

        # 4. Diagnostic Rule & Remediation Nodes (Tier 4)
        diag_stmt = select(DiagnosticFindingModel).where(DiagnosticFindingModel.comparison_id == comp.id)
        diag_res = await session.execute(diag_stmt)
        findings = diag_res.scalars().all()

        for idx, f in enumerate(findings):
            rule_node_id = f"rule-{f.category.lower().replace(' ', '_')}-{idx}"
            epistemic = "INFERRED"
            evidence_str = str(f.evidence_trail).lower()
            if "experimentally_validated" in evidence_str:
                epistemic = "EXPERIMENTALLY_VALIDATED"
            elif "observed" in evidence_str:
                epistemic = "OBSERVED"
            elif f.confidence.lower() == "high":
                epistemic = "INFERRED"
            else:
                epistemic = "HYPOTHESIS"

            nodes.append(CausalityNode(
                id=rule_node_id,
                label=f"Diagnosis: {f.category}",
                node_type="rule",
                status=epistemic,
                tier=4,
                subtitle=f"{f.confidence} Conf",
                details_summary=f"[{f.category} • {f.confidence} confidence] {f.root_cause_summary}",
                metadata={"category": f.category, "confidence": f.confidence, "root_cause": f.root_cause_summary},
            ))

            # Connect comparison -> rule AND link matching diff nodes directly -> rule
            edges.append(CausalityEdge(
                source=comp_node_id,
                target=rule_node_id,
                relation="EXPLAINED_BY",
                epistemic_status=epistemic,
                confidence=f.confidence,
                details=f.root_cause_summary or f"Rule {f.category} classified drift with {f.confidence} confidence.",
            ))

            affected = {str(p) for p in (f.affected_paths or [])}
            linked_any_diff = False
            for d_node_id, d_model in diff_nodes_info:
                if any(d_model.file_path in ap or ap in d_model.file_path for ap in affected):
                    edges.append(CausalityEdge(
                        source=d_node_id,
                        target=rule_node_id,
                        relation="EVIDENCED_BY",
                        epistemic_status=epistemic,
                        confidence=f.confidence,
                        details=f"Discrepancy '{d_model.file_path}' ({d_model.diff_type}) directly triggered diagnostic rule '{f.category}'.",
                    ))
                    linked_any_diff = True
            if not linked_any_diff and diff_nodes_info:
                d_node_id, d_model = diff_nodes_info[0]
                edges.append(CausalityEdge(
                    source=d_node_id,
                    target=rule_node_id,
                    relation="EVIDENCED_BY",
                    epistemic_status=epistemic,
                    confidence=f.confidence,
                    details=f"Observed artifact variance (starting at '{d_model.file_path}') contributed evidence for '{f.category}'.",
                ))

            # Remediation Node (Tier 4)
            rem_node_id = f"rem-{idx + 1}"
            action_txt = f.suggested_remediation or "Normalize timestamps / control SOURCE_DATE_EPOCH"
            nodes.append(CausalityNode(
                id=rem_node_id,
                label=f"Remediation #{idx + 1}: {f.category}",
                node_type="remediation",
                status="HYPOTHESIS",
                tier=4,
                subtitle="Risk Level 1/2",
                details_summary=f"Proposed action for {f.category}: {action_txt}",
                metadata={"action": action_txt, "category": f.category},
            ))
            edges.append(CausalityEdge(
                source=rule_node_id,
                target=rem_node_id,
                relation="REMEDIATED_BY",
                epistemic_status="HYPOTHESIS",
                confidence=f.confidence,
                details=f"Mitigation strategy: {action_txt}",
            ))

        graph = CausalityGraph(
            graph_id=f"graph-{comp.id[:8]}",
            comparison_id=comp.id,
            created_at=datetime.now(timezone.utc).isoformat(),
            nodes=nodes,
            edges=edges,
        )
        return graph.to_dict()
