"""Unit tests for CausalityGraphService DAG generation and DOT export."""

from datetime import datetime, timezone
import pytest

from backend.app.comparison.diff import ArtifactComparisonSummary
from backend.app.database.session import AsyncSessionLocal
from backend.app.repositories.build_repo import BuildRepository
from backend.app.repositories.comparison_repo import ComparisonRepository
from backend.app.repositories.fingerprint_repo import FingerprintRepository
from backend.app.repositories.repository_repo import ProjectRepository, RepositoryRepository
from backend.app.schemas.entities import (
    ConfidenceLevel,
    DiagnosticCategory,
    DiagnosticFindingItem,
    DiffFindingItem,
    EnvironmentFingerprintCreate,
    LocaleInfo,
    ReproducibilityVerdict,
    SystemInfo,
    ToolchainInfo,
)
from backend.app.scoring.calculator import ReproducibilityCalculator
from backend.app.services.causality_graph_service import (
    CausalityEdge,
    CausalityGraph,
    CausalityGraphService,
    CausalityNode,
)


def test_causality_graph_data_structures():
    """Verify dataclass instantiation, dict conversion, and DOT export."""
    n1 = CausalityNode(id="bld-1", label="Build 1", node_type="build", metadata={"status": "success"})
    n2 = CausalityNode(id="rule-1", label="Diagnostic Rule", node_type="rule")
    edge = CausalityEdge(
        source="bld-1",
        target="rule-1",
        relation="EXPLAINED_BY",
        epistemic_status="INFERRED",
        confidence="HIGH",
    )

    graph = CausalityGraph(
        graph_id="graph-test",
        comparison_id="comp-test",
        created_at=datetime.now(timezone.utc).isoformat(),
        nodes=[n1, n2],
        edges=[edge],
    )

    data = graph.to_dict()
    assert data["graph_id"] == "graph-test"
    assert len(data["nodes"]) == 2
    assert len(data["edges"]) == 1
    assert data["edges"][0]["epistemic_status"] == "INFERRED"

    dot = graph.to_dot()
    assert "digraph BuildDriftCausalityGraph" in dot
    assert '"bld-1" -> "rule-1"' in dot
    assert "EXPLAINED_BY [INFERRED]" in dot


@pytest.mark.asyncio
async def test_causality_graph_service_seeded_comparison():
    """Verify CausalityGraphService constructs complete DAG nodes and evidence-backed edges."""
    async with AsyncSessionLocal() as session:
        async with session.begin():
            proj = await ProjectRepository(session).get_or_create("GraphProj")
            repo = await RepositoryRepository(session).get_or_create("GraphRepo", "https://example.com/repo.git", proj.id)
            rev = await RepositoryRepository(session).record_revision(repo.id, "abcdef1234567890" * 2 + "abcdef12", "main", "Tester", "Commit")
            fp = await FingerprintRepository(session).get_or_record(
                EnvironmentFingerprintCreate(
                    fingerprint_hash="f" * 64,
                    system_info=SystemInfo(
                        os_name="Linux", os_release="6.1", os_version="6.1", kernel_release="6.1",
                        cpu_arch="x86_64", cpu_count=4, total_ram_bytes=8 * 1024**3, hostname_masked="node-1",
                    ),
                    toolchain_info=ToolchainInfo(tools={"python": {"version": "3.14.0"}}),
                    locale_info=LocaleInfo(timezone="UTC", umask="0022"),
                )
            )
            b_repo = BuildRepository(session)
            b1 = await b_repo.create_build(repo.id, rev.id, "bld-g1", fingerprint_id=fp.id)
            b2 = await b_repo.create_build(repo.id, rev.id, "bld-g2", fingerprint_id=fp.id)

            summary = ArtifactComparisonSummary(
                artifact_1_path="a.tar",
                artifact_2_path="b.tar",
                strict_sha_1="1" * 64,
                strict_sha_2="2" * 64,
                normalized_sha_1="3" * 64,
                normalized_sha_2="3" * 64,
                exact_match=False,
                normalized_match=True,
                content_agreement_ratio=1.0,
                metadata_agreement_ratio=0.0,
                total_files_evaluated=1,
                added_files_count=0,
                removed_files_count=0,
                modified_content_count=0,
                modified_metadata_count=1,
                findings=[
                    DiffFindingItem(
                        file_path="app.py",
                        diff_type="METADATA_ONLY",
                        strict_match=False,
                        normalized_match=True,
                        size_delta_bytes=0,
                        details={"mtime_1": 100, "mtime_2": 200},
                    )
                ],
            )
            diags = [
                DiagnosticFindingItem(
                    category=DiagnosticCategory.TIMESTAMP_NONDETERMINISM,
                    confidence=ConfidenceLevel.HIGH,
                    affected_paths=["app.py"],
                    root_cause_summary="mtime mismatch",
                    evidence_trail=[{"status": "experimentally_validated", "fact": "Perturbation confirmed"}],
                    suggested_remediation="Export SOURCE_DATE_EPOCH",
                )
            ]
            assessment = ReproducibilityCalculator.evaluate(summary, diags)
            comp = await ComparisonRepository(session).record_comparison(b1.id, b2.id, summary, assessment)
            from backend.app.repositories.diagnostic_repo import DiagnosticRepository
            await DiagnosticRepository(session).record_findings(comp.id, diags)

    service = CausalityGraphService()
    graph = await service.get_latest_graph()
    assert graph is not None
    node_types = {n["node_type"] for n in graph["nodes"]}
    assert {"comparison", "build", "revision", "fingerprint", "diff", "rule", "remediation"}.issubset(node_types)
    valid_epistemics = {"OBSERVED", "INFERRED", "HYPOTHESIS", "EXPERIMENTALLY_VALIDATED"}
    relations = {e["relation"] for e in graph["edges"]}
    assert "EVIDENCED_BY" in relations
    for edge in graph["edges"]:
        assert edge["epistemic_status"] in valid_epistemics
        assert edge["source_id"] == edge["source"]
        assert edge["target_id"] == edge["target"]
        assert edge["source_label"]
        assert edge["target_label"]
        assert edge["evidence"]
    assert "digraph BuildDriftCausalityGraph" in graph["dot_source"]
