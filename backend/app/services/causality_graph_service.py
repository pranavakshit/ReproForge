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
    node_type: str  # 'build', 'revision', 'fingerprint', 'diff', 'rule', 'remediation'
    status: str = "active"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CausalityEdge:
    """A directed evidence-backed relationship between two causality nodes."""

    source: str
    target: str
    relation: str  # 'TRIGGERED', 'CAPTURED_IN', 'PRODUCED_DIFF', 'EVIDENCED_BY', 'EXPLAINED_BY', 'REMEDIATED_BY'
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
        return {
            "graph_id": self.graph_id,
            "comparison_id": self.comparison_id,
            "created_at": self.created_at,
            "nodes": [asdict(n) for n in self.nodes],
            "edges": [asdict(e) for e in self.edges],
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

    async def get_latest_graph(self) -> Optional[Dict[str, Any]]:
        """Construct graph for the most recent comparison run."""
        if self._session:
            return await self._build_latest_graph(self._session)
        async with AsyncSessionLocal() as session:
            return await self._build_latest_graph(session)

    async def _build_latest_graph(self, session: AsyncSession) -> Optional[Dict[str, Any]]:
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

        # 1. Comparison Root Node
        comp_node_id = f"comp-{comp.id[:8]}"
        nodes.append(CausalityNode(
            id=comp_node_id,
            label=f"Comparison: {comp.build_id_1[:8]} vs {comp.build_id_2[:8]}",
            node_type="comparison",
            metadata={"created_at": comp.created_at.isoformat() if comp.created_at else ""},
        ))

        # 2. Build Execution Nodes
        for b_id in [comp.build_id_1, comp.build_id_2]:
            b_stmt = select(BuildExecutionModel).where(BuildExecutionModel.id == b_id)
            b_res = await session.execute(b_stmt)
            build_model = b_res.scalars().first()
            if build_model:
                b_node_id = f"build-{build_model.id[:8]}"
                nodes.append(CausalityNode(
                    id=b_node_id,
                    label=f"Build: {build_model.id[:12]}\n({build_model.profile_name})",
                    node_type="build",
                    metadata={
                        "status": build_model.status,
                        "duration": build_model.duration_seconds,
                        "profile": build_model.profile_name,
                    },
                ))
                edges.append(CausalityEdge(
                    source=comp_node_id,
                    target=b_node_id,
                    relation="EVALUATED_BUILD",
                    epistemic_status="OBSERVED",
                    confidence="HIGH",
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
                                label=f"Commit: {rev.commit_sha[:8]}\n{rev.author_name or 'Author'}",
                                node_type="revision",
                                metadata={"message": rev.commit_message, "sha": rev.commit_sha},
                            ))
                        edges.append(CausalityEdge(
                            source=rev_node_id,
                            target=b_node_id,
                            relation="TRIGGERED",
                            epistemic_status="OBSERVED",
                            confidence="HIGH",
                        ))

                # Environment Fingerprint Node
                if build_model.fingerprint_id:
                    fp_stmt = select(EnvironmentFingerprintModel).where(EnvironmentFingerprintModel.id == build_model.fingerprint_id)
                    fp_res = await session.execute(fp_stmt)
                    fp = fp_res.scalars().first()
                    if fp:
                        fp_node_id = f"fp-{fp.fingerprint_hash[:8]}"
                        if not any(n.id == fp_node_id for n in nodes):
                            os_name = fp.system_info.get("os_name", "OS") if isinstance(fp.system_info, dict) else "OS"
                            nodes.append(CausalityNode(
                                id=fp_node_id,
                                label=f"Fingerprint: {fp.fingerprint_hash[:8]}\n({os_name})",
                                node_type="fingerprint",
                                metadata={"hash": fp.fingerprint_hash},
                            ))
                        edges.append(CausalityEdge(
                            source=fp_node_id,
                            target=b_node_id,
                            relation="HOSTED_IN",
                            epistemic_status="OBSERVED",
                            confidence="HIGH",
                        ))

        # 3. Diff Finding Nodes
        diff_stmt = select(DiffFindingModel).where(DiffFindingModel.comparison_id == comp.id)
        diff_res = await session.execute(diff_stmt)
        diffs = diff_res.scalars().all()
        for idx, d in enumerate(diffs[:6]):  # top 6 diffs for clarity
            diff_node_id = f"diff-{d.id[:8]}"
            nodes.append(CausalityNode(
                id=diff_node_id,
                label=f"Diff: {d.file_path}\n[{d.diff_type}]",
                node_type="diff",
                metadata={"diff_type": d.diff_type, "file_path": d.file_path},
            ))
            edges.append(CausalityEdge(
                source=comp_node_id,
                target=diff_node_id,
                relation="PRODUCED_DIFF",
                epistemic_status="OBSERVED",
                confidence="HIGH",
            ))

        # 4. Diagnostic Rule & Remediation Nodes
        diag_stmt = select(DiagnosticFindingModel).where(DiagnosticFindingModel.comparison_id == comp.id)
        diag_res = await session.execute(diag_stmt)
        findings = diag_res.scalars().all()

        for idx, f in enumerate(findings):
            rule_node_id = f"rule-{f.category.lower().replace(' ', '_')}-{idx}"
            epistemic = "INFERRED"
            evidence_str = str(f.evidence_trail).lower()
            if "experimentally_validated" in evidence_str:
                epistemic = "EXPERIMENTALLY_VALIDATED"
            elif f.confidence.lower() == "high":
                epistemic = "INFERRED"
            else:
                epistemic = "HYPOTHESIS"

            nodes.append(CausalityNode(
                id=rule_node_id,
                label=f"Diagnosis: {f.category}\n({f.confidence} Conf)",
                node_type="rule",
                metadata={"category": f.category, "confidence": f.confidence, "root_cause": f.root_cause_summary},
            ))

            # Connect diffs or comparison to rule
            edges.append(CausalityEdge(
                source=comp_node_id,
                target=rule_node_id,
                relation="EXPLAINED_BY",
                epistemic_status=epistemic,
                confidence=f.confidence,
                details=f.root_cause_summary,
            ))

            # Remediation Node
            rem_node_id = f"rem-{idx + 1}"
            nodes.append(CausalityNode(
                id=rem_node_id,
                label=f"Remediation Proposal\n(Risk Level 1/2)",
                node_type="remediation",
                metadata={"action": f.suggested_remediation or "Normalize timestamps / control SOURCE_DATE_EPOCH"},
            ))
            edges.append(CausalityEdge(
                source=rule_node_id,
                target=rem_node_id,
                relation="REMEDIATED_BY",
                epistemic_status="HYPOTHESIS",
                confidence=f.confidence,
            ))

        graph = CausalityGraph(
            graph_id=f"graph-{comp.id[:8]}",
            comparison_id=comp.id,
            created_at=datetime.now(timezone.utc).isoformat(),
            nodes=nodes,
            edges=edges,
        )
        return graph.to_dict()
