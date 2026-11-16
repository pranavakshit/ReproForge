"""SQLAlchemy 2.0 ORM models for ReproForge domain entities."""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from backend.app.database.session import Base


def generate_uuid() -> str:
    """Generate a 36-character UUID string."""
    return str(uuid.uuid4())


def utcnow() -> datetime:
    """Return timezone-aware current UTC datetime."""
    return datetime.now(timezone.utc)


class ProjectModel(Base):
    """Project grouping repositories and reproducible build evaluations."""

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    repositories: Mapped[List["RepositoryModel"]] = relationship(
        "RepositoryModel", back_populates="project", cascade="all, delete-orphan"
    )


class RepositoryModel(Base):
    """Git repository registered for reproducible build verification."""

    __tablename__ = "repositories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    project_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    repo_url: Mapped[str] = mapped_column(String(512), nullable=False)
    repo_type: Mapped[str] = mapped_column(String(32), default="local")
    default_branch: Mapped[str] = mapped_column(String(64), default="main")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    project: Mapped[Optional["ProjectModel"]] = relationship(
        "ProjectModel", back_populates="repositories"
    )
    revisions: Mapped[List["SourceRevisionModel"]] = relationship(
        "SourceRevisionModel", back_populates="repository", cascade="all, delete-orphan"
    )
    build_executions: Mapped[List["BuildExecutionModel"]] = relationship(
        "BuildExecutionModel", back_populates="repository", cascade="all, delete-orphan"
    )


class SourceRevisionModel(Base):
    """Specific Git commit revision evaluated for reproducibility."""

    __tablename__ = "source_revisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    repository_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    commit_sha: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    branch_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    author_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    commit_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    committed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    repository: Mapped["RepositoryModel"] = relationship(
        "RepositoryModel", back_populates="revisions"
    )
    build_executions: Mapped[List["BuildExecutionModel"]] = relationship(
        "BuildExecutionModel", back_populates="revision", cascade="all, delete-orphan"
    )


class EnvironmentFingerprintModel(Base):
    """Structured capture of the build host and container runtime environment."""

    __tablename__ = "environment_fingerprints"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    fingerprint_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    system_info: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)
    toolchain_info: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)
    locale_info: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)
    environment_variables: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)
    container_runtime_info: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    build_executions: Mapped[List["BuildExecutionModel"]] = relationship(
        "BuildExecutionModel", back_populates="fingerprint"
    )


class BuildExecutionModel(Base):
    """A single isolated build invocation of a source revision."""

    __tablename__ = "build_executions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    repository_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    revision_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("source_revisions.id", ondelete="CASCADE"), nullable=False
    )
    fingerprint_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("environment_fingerprints.id", ondelete="SET NULL"), nullable=True
    )
    profile_name: Mapped[str] = mapped_column(String(64), default="default-isolated")
    status: Mapped[str] = mapped_column(String(32), default="queued")
    exit_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    log_file_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    repository: Mapped["RepositoryModel"] = relationship(
        "RepositoryModel", back_populates="build_executions"
    )
    revision: Mapped["SourceRevisionModel"] = relationship(
        "SourceRevisionModel", back_populates="build_executions"
    )
    fingerprint: Mapped[Optional["EnvironmentFingerprintModel"]] = relationship(
        "EnvironmentFingerprintModel", back_populates="build_executions"
    )
    artifacts: Mapped[List["ArtifactItemModel"]] = relationship(
        "ArtifactItemModel", back_populates="build_execution", cascade="all, delete-orphan"
    )


class ArtifactItemModel(Base):
    """An individual build artifact (image, tar, zip, binary, or directory)."""

    __tablename__ = "artifact_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    build_execution_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("build_executions.id", ondelete="CASCADE"), nullable=False
    )
    relative_path: Mapped[str] = mapped_column(String(512), nullable=False)
    artifact_type: Mapped[str] = mapped_column(String(64), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    strict_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    normalized_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    metadata_json: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    build_execution: Mapped["BuildExecutionModel"] = relationship(
        "BuildExecutionModel", back_populates="artifacts"
    )


class BuildComparisonModel(Base):
    """Pairwise comparison record of two independent build executions."""

    __tablename__ = "build_comparisons"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    build_id_1: Mapped[str] = mapped_column(
        String(36), ForeignKey("build_executions.id", ondelete="CASCADE"), nullable=False
    )
    build_id_2: Mapped[str] = mapped_column(
        String(36), ForeignKey("build_executions.id", ondelete="CASCADE"), nullable=False
    )
    mode: Mapped[str] = mapped_column(String(32), default="strict")
    exact_match: Mapped[bool] = mapped_column(Boolean, default=False)
    normalized_match: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    findings: Mapped[List["DiffFindingModel"]] = relationship(
        "DiffFindingModel", back_populates="comparison", cascade="all, delete-orphan"
    )
    diagnostics: Mapped[List["DiagnosticFindingModel"]] = relationship(
        "DiagnosticFindingModel", back_populates="comparison", cascade="all, delete-orphan"
    )
    score: Mapped[Optional["ReproducibilityScoreModel"]] = relationship(
        "ReproducibilityScoreModel", back_populates="comparison", uselist=False, cascade="all, delete-orphan"
    )


class DiffFindingModel(Base):
    """A granular discrepancy observed during artifact differential analysis."""

    __tablename__ = "diff_findings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    comparison_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("build_comparisons.id", ondelete="CASCADE"), nullable=False
    )
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    diff_type: Mapped[str] = mapped_column(String(32), nullable=False)
    strict_match: Mapped[bool] = mapped_column(Boolean, default=False)
    normalized_match: Mapped[bool] = mapped_column(Boolean, default=False)
    size_delta_bytes: Mapped[int] = mapped_column(Integer, default=0)
    details: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)

    comparison: Mapped["BuildComparisonModel"] = relationship(
        "BuildComparisonModel", back_populates="findings"
    )


class DiagnosticFindingModel(Base):
    """An explainable diagnostic conclusion produced by the diagnostic engine."""

    __tablename__ = "diagnostic_findings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    comparison_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("build_comparisons.id", ondelete="CASCADE"), nullable=False
    )
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[str] = mapped_column(String(32), nullable=False)
    affected_paths: Mapped[List[str]] = mapped_column(JSON, default=list)
    root_cause_summary: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_trail: Mapped[List[Dict[str, Any]]] = mapped_column(JSON, default=list)
    suggested_remediation: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    comparison: Mapped["BuildComparisonModel"] = relationship(
        "BuildComparisonModel", back_populates="diagnostics"
    )


class ReproducibilityScoreModel(Base):
    """Reproducibility scoring measurements and composite index."""

    __tablename__ = "reproducibility_scores"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    comparison_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("build_comparisons.id", ondelete="CASCADE"), nullable=False
    )
    exact_match: Mapped[bool] = mapped_column(Boolean, nullable=False)
    normalized_match: Mapped[bool] = mapped_column(Boolean, nullable=False)
    content_agreement_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    metadata_agreement_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    environment_consistency_score: Mapped[float] = mapped_column(Float, nullable=False)
    composite_reproducibility_index: Mapped[float] = mapped_column(Float, nullable=False)
    verdict: Mapped[str] = mapped_column(String(64), nullable=False)
    unexplained_differences_count: Mapped[int] = mapped_column(Integer, default=0)

    comparison: Mapped["BuildComparisonModel"] = relationship(
        "BuildComparisonModel", back_populates="score"
    )
