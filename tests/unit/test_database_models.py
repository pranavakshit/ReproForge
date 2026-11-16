"""Unit tests for SQLAlchemy models and async database session."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from backend.app.database.session import Base
from backend.app.models.entities import (
    EnvironmentFingerprintModel,
    ProjectModel,
    RepositoryModel,
    SourceRevisionModel,
)


@pytest.fixture
async def test_session():
    """In-memory SQLite async engine for isolated test executions."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_create_project_and_repository(test_session: AsyncSession):
    """Test relational creation of project and git repository models."""
    project = ProjectModel(name="ReproForge Test Suite", description="Test project")
    test_session.add(project)
    await test_session.commit()

    repo = RepositoryModel(
        project_id=project.id,
        name="test-repo",
        repo_url="https://github.com/example/test-repo.git",
        repo_type="github",
        default_branch="main",
    )
    test_session.add(repo)
    await test_session.commit()

    result = await test_session.execute(
        select(RepositoryModel).where(RepositoryModel.name == "test-repo")
    )
    fetched_repo = result.scalar_one_or_none()
    assert fetched_repo is not None
    assert fetched_repo.project_id == project.id
    assert fetched_repo.default_branch == "main"


@pytest.mark.asyncio
async def test_create_environment_fingerprint_record(test_session: AsyncSession):
    """Test persistence of structured environment fingerprint record."""
    fp_record = EnvironmentFingerprintModel(
        fingerprint_hash="a" * 64,
        system_info={"os_name": "Linux", "cpu_arch": "x86_64"},
        toolchain_info={"tools": {"python": {"version": "3.12"}}},
        locale_info={"timezone": "UTC", "umask": "0022"},
        environment_variables={"LANG": "C.UTF-8"},
        container_runtime_info={"available": True},
    )
    test_session.add(fp_record)
    await test_session.commit()

    result = await test_session.execute(
        select(EnvironmentFingerprintModel).where(
            EnvironmentFingerprintModel.fingerprint_hash == "a" * 64
        )
    )
    fetched_fp = result.scalar_one_or_none()
    assert fetched_fp is not None
    assert fetched_fp.system_info["os_name"] == "Linux"
    assert fetched_fp.toolchain_info["tools"]["python"]["version"] == "3.12"
