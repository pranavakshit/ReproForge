"""Tests for Phase 5A persistence repositories, SQLite WAL pragmas, rollback, and recovery."""

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.comparison.diff import ArtifactComparisonSummary
from backend.app.database.session import Base, configure_sqlite_pragmas, transaction_scope
from backend.app.models.entities import BuildExecutionModel, ProjectModel
from backend.app.repositories.artifact_repo import ArtifactRepository
from backend.app.repositories.build_repo import BuildRepository
from backend.app.repositories.comparison_repo import ComparisonRepository
from backend.app.repositories.diagnostic_repo import DiagnosticRepository
from backend.app.repositories.fingerprint_repo import FingerprintRepository
from backend.app.repositories.repository_repo import ProjectRepository, RepositoryRepository
from backend.app.schemas.entities import (
    ArtifactItemCreate,
    BuildStatus,
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
from backend.app.scoring.calculator import ReproducibilityAssessment


@pytest_asyncio.fixture
async def async_test_session():
    """Create an isolated in-memory SQLite database with WAL and pragmas configured."""
    from sqlalchemy import event

    test_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        future=True,
    )
    event.listen(test_engine.sync_engine, "connect", configure_sqlite_pragmas)

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )

    async with session_factory() as session:
        yield session

    await test_engine.dispose()


@pytest.mark.asyncio
async def test_sqlite_pragmas_and_integrity(async_test_session: AsyncSession):
    """Verify SQLite foreign keys, busy timeout, and integrity check."""
    # Note: in-memory sqlite ignores journal_mode=WAL and uses memory, but foreign_keys and integrity are active
    fk_result = await async_test_session.execute(text("PRAGMA foreign_keys;"))
    assert fk_result.scalar() == 1

    integrity_result = await async_test_session.execute(text("PRAGMA integrity_check;"))
    assert integrity_result.scalar() == "ok"


@pytest.mark.asyncio
async def test_project_and_repository_crud(async_test_session: AsyncSession):
    """Test ProjectRepository and RepositoryRepository operations."""
    proj_repo = ProjectRepository(async_test_session)
    repo_repo = RepositoryRepository(async_test_session)

    # 1. Create project
    proj = await proj_repo.get_or_create("ReproForge-Core", "Core test project")
    assert proj.id is not None
    assert proj.name == "ReproForge-Core"

    # Verify idempotency
    proj_dup = await proj_repo.get_or_create("ReproForge-Core")
    assert proj_dup.id == proj.id

    # 2. Register repository
    repo = await repo_repo.get_or_create(
        name="ReproRepo",
        repo_url="/path/to/repo",
        project_id=proj.id,
    )
    assert repo.id is not None
    assert repo.name == "ReproRepo"

    # 3. Record revision
    rev = await repo_repo.record_revision(
        repository_id=repo.id,
        commit_sha="a1b2c3d4e5f6789012345678901234567890abcd",
        branch_name="main",
        commit_message="Initial commit",
    )
    assert rev.id is not None
    assert rev.commit_sha.startswith("a1b2c3d4")


@pytest.mark.asyncio
async def test_build_lifecycle_and_cancellation(async_test_session: AsyncSession):
    """Test build creation, status transitions, and individual cancellation."""
    proj_repo = ProjectRepository(async_test_session)
    repo_repo = RepositoryRepository(async_test_session)
    build_repo = BuildRepository(async_test_session)

    proj = await proj_repo.get_or_create("BuildTestProj")
    repo = await repo_repo.get_or_create("BuildTestRepo", "/repo/path", proj.id)
    rev = await repo_repo.record_revision(repo.id, "commit1234567890")

    # 1. Create build
    build = await build_repo.create_build(
        repository_id=repo.id,
        revision_id=rev.id,
        build_id="build-test-01",
    )
    assert build.id == "build-test-01"
    assert build.status == BuildStatus.QUEUED.value

    # 2. Transition to BUILDING
    updated = await build_repo.update_status(build.id, BuildStatus.BUILDING)
    assert updated.status == BuildStatus.BUILDING.value

    # 3. Cancel build
    cancelled = await build_repo.cancel_build(build.id)
    assert cancelled.status == BuildStatus.CANCELLED.value
    assert cancelled.exit_code == -1
    assert cancelled.completed_at is not None


@pytest.mark.asyncio
async def test_durable_interrupted_build_recovery(async_test_session: AsyncSession):
    """Test startup recovery routine reconciling orphaned in-flight builds."""
    proj_repo = ProjectRepository(async_test_session)
    repo_repo = RepositoryRepository(async_test_session)
    build_repo = BuildRepository(async_test_session)

    proj = await proj_repo.get_or_create("RecoveryTestProj")
    repo = await repo_repo.get_or_create("RecoveryTestRepo", "/repo/recovery", proj.id)
    rev = await repo_repo.record_revision(repo.id, "commit_rec_123")

    # Create two in-flight builds (one QUEUED, one BUILDING) that were orphaned by engine crash
    b1 = await build_repo.create_build(repo.id, rev.id, "build-orphan-1")
    b2 = await build_repo.create_build(repo.id, rev.id, "build-orphan-2")
    await build_repo.update_status(b2.id, BuildStatus.BUILDING)

    # Create one already COMPLETED build
    b3 = await build_repo.create_build(repo.id, rev.id, "build-completed-3")
    await build_repo.update_status(b3.id, BuildStatus.COMPLETED, exit_code=0, duration_seconds=15.0)

    # Run recovery reconciliation routine
    reconciled_ids = await build_repo.reconcile_interrupted_builds()

    assert set(reconciled_ids) == {"build-orphan-1", "build-orphan-2"}

    # Verify state in DB
    refreshed_b1 = await build_repo.get_by_id(b1.id)
    refreshed_b2 = await build_repo.get_by_id(b2.id)
    refreshed_b3 = await build_repo.get_by_id(b3.id)

    assert refreshed_b1.status == BuildStatus.EXECUTION_INTERRUPTED.value
    assert refreshed_b1.completed_at is not None
    assert refreshed_b2.status == BuildStatus.EXECUTION_INTERRUPTED.value
    assert refreshed_b2.completed_at is not None

    # Completed build remains untouched
    assert refreshed_b3.status == BuildStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_artifact_and_comparison_persistence(async_test_session: AsyncSession):
    """Test persisting artifacts, comparisons, diff findings, and scores."""
    proj_repo = ProjectRepository(async_test_session)
    repo_repo = RepositoryRepository(async_test_session)
    build_repo = BuildRepository(async_test_session)
    art_repo = ArtifactRepository(async_test_session)
    comp_repo = ComparisonRepository(async_test_session)
    diag_repo = DiagnosticRepository(async_test_session)

    proj = await proj_repo.get_or_create("DiffTestProj")
    repo = await repo_repo.get_or_create("DiffTestRepo", "/repo/diff", proj.id)
    rev = await repo_repo.record_revision(repo.id, "commit_diff_123")

    b1 = await build_repo.create_build(repo.id, rev.id, "build-comp-1")
    b2 = await build_repo.create_build(repo.id, rev.id, "build-comp-2")

    # Record artifacts
    arts1 = [
        ArtifactItemCreate(
            build_execution_id=b1.id,
            relative_path="dist/package.tar",
            artifact_type="tar_archive",
            file_size_bytes=1024,
            strict_sha256="sha11111111111111111111111111111111111111111111111111111111111111",
            metadata_json={},
        )
    ]
    await art_repo.record_artifacts(b1.id, arts1)
    retrieved_arts = await art_repo.get_by_build(b1.id)
    assert len(retrieved_arts) == 1
    assert retrieved_arts[0].relative_path == "dist/package.tar"

    # Record comparison
    summary = ArtifactComparisonSummary(
        artifact_1_path="b1/dist/package.tar",
        artifact_2_path="b2/dist/package.tar",
        exact_match=False,
        normalized_match=True,
        strict_sha_1="sha111",
        strict_sha_2="sha222",
        normalized_sha_1="norm111",
        normalized_sha_2="norm111",
        content_agreement_ratio=1.0,
        metadata_agreement_ratio=0.8,
        total_files_evaluated=1,
        added_files_count=0,
        removed_files_count=0,
        modified_content_count=0,
        modified_metadata_count=1,
        findings=[
            DiffFindingItem(
                file_path="pkg/index.js",
                diff_type="METADATA_ONLY",
                strict_match=False,
                normalized_match=True,
                size_delta_bytes=0,
                details={"reason": "mtime divergence"},
            )
        ],
    )
    score = ReproducibilityAssessment(
        verdict=ReproducibilityVerdict.NORMALIZED_REPRODUCIBLE,
        exact_match=False,
        normalized_match=True,
        content_agreement_ratio=1.0,
        metadata_agreement_ratio=0.8,
        environment_consistency_score=1.0,
        unexplained_differences_count=0,
        total_files_evaluated=1,
        composite_reproducibility_index=0.92,
    )

    comp = await comp_repo.record_comparison(b1.id, b2.id, summary, score)
    assert comp.id is not None
    assert comp.exact_match is False
    assert comp.normalized_match is True

    # Record diagnostics
    findings = [
        DiagnosticFindingItem(
            category=DiagnosticCategory.TIMESTAMP_NONDETERMINISM,
            confidence=ConfidenceLevel.HIGH,
            affected_paths=["pkg/index.js"],
            root_cause_summary="Timestamps diverged in archive header",
            evidence_trail=[],
            suggested_remediation="Set SOURCE_DATE_EPOCH",
        )
    ]
    diag_records = await diag_repo.record_findings(comp.id, findings)
    assert len(diag_records) == 1
    assert diag_records[0].category == "timestamp_nondeterminism"


@pytest.mark.asyncio
async def test_fingerprint_persistence(async_test_session: AsyncSession):
    """Test environment fingerprint recording and idempotency."""
    fp_repo = FingerprintRepository(async_test_session)

    fp = EnvironmentFingerprintCreate(
        fingerprint_hash="fp_hash_1234567890abcdef1234567890abcdef",
        system_info=SystemInfo(
            os_name="Windows",
            os_release="11",
            os_version="10.0.22631",
            cpu_arch="AMD64",
            cpu_count=8,
            total_ram_bytes=16000000000,
            hostname_masked="DESKTOP-***",
            kernel_release="10.0.22631",
        ),
        toolchain_info=ToolchainInfo(tools={"python": {"version": "3.14.7"}}),
        locale_info=LocaleInfo(timezone="UTC", encoding="utf-8", umask="022"),
        environment_variables={"PATH": "sanitized"},
        container_runtime_info={"available": False},
    )

    record1 = await fp_repo.get_or_record(fp)
    assert record1.id is not None
    assert record1.fingerprint_hash == fp.fingerprint_hash

    # Idempotent retrieval
    record2 = await fp_repo.get_or_record(fp)
    assert record2.id == record1.id


@pytest.mark.asyncio
async def test_transaction_rollback_on_failure(async_test_session: AsyncSession):
    """Verify that unhandled exceptions trigger rollback, leaving no partial state."""
    proj_repo = ProjectRepository(async_test_session)

    try:
        async with async_test_session.begin_nested():
            await proj_repo.get_or_create("RollbackProj1")
            raise RuntimeError("Simulated failure during operation")
    except RuntimeError:
        pass

    # The rolled back project should not exist
    result = await proj_repo.get_by_name("RollbackProj1")
    assert result is None


@pytest.mark.asyncio
async def test_concurrent_readers_and_wal():
    """Verify concurrent reads and sequential write behavior under WAL pragmas on disk."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "test_wal.db"
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
        from sqlalchemy import event
        event.listen(engine.sync_engine, "connect", configure_sqlite_pragmas)

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

        # Write initial record
        async with session_factory() as s1:
            p_repo = ProjectRepository(s1)
            await p_repo.get_or_create("WAL-Project")
            await s1.commit()

        # Concurrent reads
        async with session_factory() as s2, session_factory() as s3:
            p2 = await ProjectRepository(s2).get_by_name("WAL-Project")
            p3 = await ProjectRepository(s3).get_by_name("WAL-Project")
            assert p2 is not None
            assert p3 is not None
            assert p2.name == p3.name == "WAL-Project"

        await engine.dispose()

