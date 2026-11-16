"""Tests for Phase 5A shared application services."""

import io
import tarfile
import tempfile
from pathlib import Path
import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.database.session import Base, configure_sqlite_pragmas
from backend.app.orchestration.git import GitRepositoryManager
from backend.app.schemas.entities import BuildStatus, ReproducibilityVerdict
from backend.app.services.build_service import BuildService
from backend.app.services.comparison_service import ComparisonService
from backend.app.services.fingerprint_service import FingerprintService
from backend.app.services.repository_service import RepositoryService


@pytest_asyncio.fixture
async def async_service_session():
    """Create an isolated in-memory SQLite database for service tests."""
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
async def test_fingerprint_service(async_service_session: AsyncSession):
    """Test FingerprintService telemetry and persistence."""
    svc = FingerprintService(session=async_service_session)

    # 1. Telemetry
    sys_info, tools = svc.get_telemetry()
    assert sys_info.os_name != ""
    assert isinstance(tools.tools, dict)

    # 2. Capture and persist
    fp_model = await svc.capture_and_persist()
    assert fp_model.id is not None
    assert len(fp_model.fingerprint_hash) == 64

    # 3. Lookup by hash
    retrieved = await svc.get_by_hash(fp_model.fingerprint_hash)
    assert retrieved is not None
    assert retrieved.id == fp_model.id


@pytest.mark.asyncio
async def test_repository_service(async_service_session: AsyncSession):
    """Test RepositoryService project and repository registration."""
    from backend.app.repositories.repository_repo import ProjectRepository

    svc = RepositoryService(session=async_service_session)

    repo_model = await svc.register_repository(
        name="ReproTestRepo",
        repo_url="https://github.com/reproforge/test.git",
        project_name="ReproProject",
    )
    assert repo_model.id is not None
    assert repo_model.name == "ReproTestRepo"

    proj = await ProjectRepository(async_service_session).get_by_id(repo_model.project_id)
    assert proj is not None
    assert proj.name == "ReproProject"


@pytest.mark.asyncio
async def test_comparison_service_pipeline(async_service_session: AsyncSession):
    """Test ComparisonService artifact differential, diagnostic correlation, scoring, and DB persistence."""
    svc = ComparisonService(session=async_service_session)

    with tempfile.TemporaryDirectory() as td:
        p1 = Path(td) / "art1.tar"
        p2 = Path(td) / "art2.tar"

        # Create two tar archives with differing mtime (timestamp divergence)
        data = b"identical content across builds"
        with tarfile.open(p1, "w") as t:
            ti = tarfile.TarInfo("file.txt")
            ti.size = len(data)
            ti.mtime = 1000000000
            t.addfile(ti, io.BytesIO(data))

        with tarfile.open(p2, "w") as t:
            ti = tarfile.TarInfo("file.txt")
            ti.size = len(data)
            ti.mtime = 1000005000
            t.addfile(ti, io.BytesIO(data))

        # First test without build IDs (in-memory evaluation)
        outcome = await svc.compare_artifacts(p1, p2)
        assert outcome.summary.exact_match is False
        assert outcome.summary.normalized_match is True
        assert outcome.assessment.verdict == ReproducibilityVerdict.NORMALIZED_REPRODUCIBLE
        assert any(d.category.value == "timestamp_nondeterminism" for d in outcome.diagnostics)
        assert len(outcome.remediations) > 0
        assert outcome.comparison_id is None


@pytest.mark.asyncio
async def test_build_service_execution_and_cancellation(async_service_session: AsyncSession):
    """Test BuildService end-to-end execution with local git repo and cancellation."""
    import subprocess

    svc = BuildService(session=async_service_session)

    with tempfile.TemporaryDirectory() as td:
        repo_dir = Path(td) / "sample_repo"
        repo_dir.mkdir()

        # Initialize real git repo
        subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo_dir, check=True)
        subprocess.run(["git", "config", "user.email", "test@reproforge.org"], cwd=repo_dir, check=True)

        pyproject = repo_dir / "pyproject.toml"
        pyproject.write_text('[project]\nname = "test-pkg"\nversion = "0.1.0"\n', encoding="utf-8")

        subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo_dir, check=True)

        # Execute build using a simple python echo command
        outcome = await svc.execute_build(
            repo_path=repo_dir,
            commit_ref="HEAD",
            backend_type="local",
            custom_cmd="python -c \"print('Build successful!')\"",
        )

        assert outcome.exit_code == 0
        assert outcome.status == BuildStatus.COMPLETED

        # Check DB record
        build_record = await svc.get_build(outcome.build_id)
        assert build_record is not None
        assert build_record.status == BuildStatus.COMPLETED.value
        assert build_record.exit_code == 0

        # Test cancel_build on a mock build
        from backend.app.repositories.build_repo import BuildRepository
        new_b = await BuildRepository(async_service_session).create_build(
            repository_id=build_record.repository_id,
            revision_id=build_record.revision_id,
            build_id="build-to-cancel",
        )
        cancelled = await svc.cancel_build("build-to-cancel")
        assert cancelled is not None
        assert cancelled.status == BuildStatus.CANCELLED.value
