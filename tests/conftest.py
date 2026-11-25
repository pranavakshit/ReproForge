"""Global pytest isolation fixtures ensuring tests never pollute production SQLite or artifact dirs."""

import asyncio
from pathlib import Path
import pytest
import pytest_asyncio

from backend.app.core.config import settings
import backend.app.database.session as session_mod


@pytest.fixture(autouse=True, scope="function")
def isolated_runtime_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Redirect database, build workspaces, and artifact storage to ephemeral temp dirs per test."""
    db_path = tmp_path / "test_reproforge.db"
    db_url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    ws_dir = tmp_path / "workspaces"
    art_dir = tmp_path / "artifacts"
    data_dir = tmp_path / "data"
    ws_dir.mkdir(parents=True, exist_ok=True)
    art_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(settings, "DATABASE_URL", db_url)
    monkeypatch.setattr(settings, "WORKSPACES_DIR", ws_dir)
    monkeypatch.setattr(settings, "ARTIFACTS_DIR", art_dir)
    monkeypatch.setattr(settings, "DATA_DIR", data_dir)

    # Also propagate via process env so child subprocesses (e.g. daemon integration tests) isolate DB & dirs
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("WORKSPACES_DIR", str(ws_dir))
    monkeypatch.setenv("ARTIFACTS_DIR", str(art_dir))
    monkeypatch.setenv("DATA_DIR", str(data_dir))

    test_engine = session_mod.configure_database_engine(db_url)

    async def _init():
        async with test_engine.begin() as conn:
            await conn.run_sync(session_mod.Base.metadata.create_all)

    asyncio.run(_init())
    try:
        yield tmp_path
    finally:
        asyncio.run(test_engine.dispose())
