"""Ephemeral build workspace manager."""

import shutil
import uuid
from pathlib import Path
from typing import Optional

from backend.app.core.config import settings
from backend.app.core.logging import get_logger

logger = get_logger("reproforge.execution.workspace")


class WorkspaceManager:
    """Allocates, populates, and disposes isolated ephemeral build directories."""

    def __init__(self, base_workspaces_dir: Optional[Path] = None) -> None:
        self.base_dir = base_workspaces_dir or settings.WORKSPACES_DIR
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def allocate_workspace(self, build_id: Optional[str] = None) -> Path:
        """Create a dedicated, clean directory for a build execution."""
        bid = build_id or str(uuid.uuid4())
        workspace_path = self.base_dir / bid
        if workspace_path.exists():
            shutil.rmtree(workspace_path, ignore_errors=True)
        workspace_path.mkdir(parents=True, exist_ok=True)
        source_dir = workspace_path / "source"
        source_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Allocated workspace for build {bid}: {workspace_path}")
        return workspace_path

    def populate_source(self, workspace_path: Path, source_path: Path) -> Path:
        """Copy source code into the isolated workspace directory."""
        source_dest = workspace_path / "source"
        # Copy directory tree excluding common unnecessary files
        shutil.copytree(
            source_path,
            source_dest,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", "*.pyc"),
        )
        return source_dest

    def cleanup_workspace(self, workspace_path: Path) -> None:
        """Safely dispose of ephemeral workspace directory."""
        if workspace_path.exists():
            try:
                shutil.rmtree(workspace_path, ignore_errors=True)
                logger.info(f"Cleaned up workspace: {workspace_path}")
            except Exception as e:
                logger.warning(f"Failed to fully clean workspace {workspace_path}: {e}")
