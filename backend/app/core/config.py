"""ReproForge configuration management."""

import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings with environment variable override support."""

    APP_NAME: str = "ReproForge"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False

    # Base filesystem paths
    BASE_DIR: Path = Path(__file__).resolve().parent.parent.parent.parent
    DATA_DIR: Path = BASE_DIR / "data"
    WORKSPACES_DIR: Path = BASE_DIR / "build_workspaces"
    ARTIFACTS_DIR: Path = BASE_DIR / "collected_artifacts"

    # Database configuration (defaults to aiosqlite for local development)
    DATABASE_URL: str = f"sqlite+aiosqlite:///{DATA_DIR / 'reproforge.db'}"

    # Execution defaults
    DEFAULT_BUILD_TIMEOUT_SECONDS: int = 1200
    MAX_CONCURRENT_BUILDS: int = 2

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def init_directories(self) -> None:
        """Ensure all required runtime directories exist."""
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)
        self.ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.init_directories()
