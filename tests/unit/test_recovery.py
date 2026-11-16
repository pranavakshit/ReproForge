"""Tests for Phase 5A post-crash durable recovery and orphan reconciliation."""

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.database.session import Base, configure_sqlite_pragmas
from backend.app.models.entities import BuildExecutionModel
from backend.app.repositories.build_repo import BuildRepository
from backend.app.repositories.repository_repo import ProjectRepository, RepositoryRepository
from backend.app.schemas.entities import BuildStatus
from backend.app.services.build_service import BuildService


@pytest_asyncio.fixture
async def recovery_session():
    """Create isolated SQLite in-memory DB for crash recovery testing."""
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
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
async def test_recovery_reconciles_orphaned_jobs_only(recovery_session: AsyncSession):
    """Verify that startup recovery reconciles only in-flight jobs and never touches terminal ones."""
    proj_repo = ProjectRepository(recovery_session)
    repo_repo = RepositoryRepository(recovery_session)
    build_repo = BuildRepository(recovery_session)
    build_service = BuildService(session=recovery_session)

    proj = await proj_repo.get_or_create("CrashTestProject")
    repo = await repo_repo.get_or_create("CrashTestRepo", "/path/to/repo", proj.id)
    rev = await repo_repo.record_revision(repo.id, "commit_crash_sha")

    # 1. Seed builds across various states:
    # In-flight (must be recovered)
    b_queued = await build_repo.create_build(repo.id, rev.id, "b-queued")
    b_building = await build_repo.create_build(repo.id, rev.id, "b-building")
    await build_repo.update_status(b_building.id, BuildStatus.BUILDING)

    # Terminal states (must NOT be touched)
    b_completed = await build_repo.create_build(repo.id, rev.id, "b-completed")
    await build_repo.update_status(b_completed.id, BuildStatus.COMPLETED, exit_code=0)

    b_failed = await build_repo.create_build(repo.id, rev.id, "b-failed")
    await build_repo.update_status(b_failed.id, BuildStatus.FAILED, exit_code=1)

    b_cancelled = await build_repo.create_build(repo.id, rev.id, "b-cancelled")
    await build_repo.update_status(b_cancelled.id, BuildStatus.CANCELLED)

    # 2. Execute recovery through BuildService
    reconciled_ids = await build_service.reconcile_crashed_builds()

    assert set(reconciled_ids) == {"b-queued", "b-building"}

    # 3. Assert states
    refreshed_queued = await build_repo.get_by_id(b_queued.id)
    refreshed_building = await build_repo.get_by_id(b_building.id)
    refreshed_completed = await build_repo.get_by_id(b_completed.id)
    refreshed_failed = await build_repo.get_by_id(b_failed.id)
    refreshed_cancelled = await build_repo.get_by_id(b_cancelled.id)

    assert refreshed_queued.status == BuildStatus.EXECUTION_INTERRUPTED.value
    assert refreshed_queued.completed_at is not None

    assert refreshed_building.status == BuildStatus.EXECUTION_INTERRUPTED.value
    assert refreshed_building.completed_at is not None

    # Terminal states preserved exactly
    assert refreshed_completed.status == BuildStatus.COMPLETED.value
    assert refreshed_failed.status == BuildStatus.FAILED.value
    assert refreshed_cancelled.status == BuildStatus.CANCELLED.value
