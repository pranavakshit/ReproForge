"""Pydantic schemas for ReproForge domain entities."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class RepositoryType(str, Enum):
    LOCAL = "local"
    GITHUB = "github"


class BuildStatus(str, Enum):
    QUEUED = "queued"
    FINGERPRINTING = "fingerprinting"
    PROVISIONING = "provisioning"
    BUILDING = "building"
    COLLECTING = "collecting"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    EXECUTION_INTERRUPTED = "execution_interrupted"


class ComparisonMode(str, Enum):
    STRICT = "strict"
    NORMALIZED = "normalized"


class DiagnosticCategory(str, Enum):
    TIMESTAMP_NONDETERMINISM = "timestamp_nondeterminism"
    DEPENDENCY_VARIATION = "dependency_variation"
    ENVIRONMENT_LEAKAGE = "environment_leakage"
    TOOLCHAIN_VARIATION = "toolchain_variation"
    FILESYSTEM_ORDERING = "filesystem_ordering"
    METADATA_VARIATION = "metadata_variation"
    BASE_IMAGE_VARIATION = "base_image_variation"
    GENERATED_CONTENT = "generated_content"
    PLATFORM_VARIATION = "platform_variation"
    EXTERNAL_INPUT_VARIATION = "external_input_variation"
    UNKNOWN_VARIATION = "unknown_variation"


class ConfidenceLevel(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INCONCLUSIVE = "inconclusive"


class ReproducibilityVerdict(str, Enum):
    BIT_FOR_BIT_REPRODUCIBLE = "bit_for_bit_reproducible"
    NORMALIZED_REPRODUCIBLE = "normalized_reproducible"
    PARTIALLY_REPRODUCIBLE = "partially_reproducible"
    NON_REPRODUCIBLE = "non_reproducible"
    INCONCLUSIVE = "inconclusive"


# --- Environment Fingerprinting Schemas ---

class SystemInfo(BaseModel):
    os_name: str
    os_release: str
    os_version: str
    kernel_release: str
    cpu_arch: str
    cpu_count: int
    total_ram_bytes: int
    hostname_masked: str


class ToolchainInfo(BaseModel):
    tools: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="Tool name mapped to version and capability attributes",
    )


class LocaleInfo(BaseModel):
    lang: Optional[str] = None
    lc_all: Optional[str] = None
    timezone: str
    umask: str


class EnvironmentFingerprintBase(BaseModel):
    fingerprint_hash: str
    system_info: SystemInfo
    toolchain_info: ToolchainInfo
    locale_info: LocaleInfo
    environment_variables: Dict[str, str] = Field(default_factory=dict)
    container_runtime_info: Dict[str, Any] = Field(default_factory=dict)
    captured_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class EnvironmentFingerprintCreate(EnvironmentFingerprintBase):
    pass


class EnvironmentFingerprintRead(EnvironmentFingerprintBase):
    id: str
    model_config = ConfigDict(from_attributes=True)


# --- Repository & Revision Schemas ---

class RepositoryCreate(BaseModel):
    name: str
    repo_url: str
    repo_type: RepositoryType = RepositoryType.LOCAL
    default_branch: str = "main"


class RepositoryRead(BaseModel):
    id: str
    name: str
    repo_url: str
    repo_type: RepositoryType
    default_branch: str
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class SourceRevisionCreate(BaseModel):
    repository_id: str
    commit_sha: str
    branch_name: Optional[str] = None
    author_name: Optional[str] = None
    commit_message: Optional[str] = None
    committed_at: Optional[datetime] = None


class SourceRevisionRead(SourceRevisionCreate):
    id: str
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


# --- Build Execution Schemas ---

class BuildExecutionCreate(BaseModel):
    repository_id: str
    revision_id: str
    profile_name: str = "default-isolated"
    timeout_seconds: int = 1200


class BuildExecutionRead(BaseModel):
    id: str
    repository_id: str
    revision_id: str
    profile_name: str
    status: BuildStatus
    exit_code: Optional[int] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    log_file_path: Optional[str] = None
    fingerprint_id: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


# --- Artifact Schemas ---

class ArtifactItemCreate(BaseModel):
    build_execution_id: str
    relative_path: str
    artifact_type: str
    file_size_bytes: int
    strict_sha256: str
    normalized_sha256: Optional[str] = None
    metadata_json: Dict[str, Any] = Field(default_factory=dict)


class ArtifactItemRead(ArtifactItemCreate):
    id: str
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


# --- Differential & Diagnostic Schemas ---

class DiffFindingItem(BaseModel):
    file_path: str
    diff_type: str  # ADDED, REMOVED, MODIFIED, METADATA_ONLY
    strict_match: bool
    normalized_match: bool
    size_delta_bytes: int
    details: Dict[str, Any] = Field(default_factory=dict)


class DiagnosticFindingItem(BaseModel):
    category: DiagnosticCategory
    confidence: ConfidenceLevel
    affected_paths: List[str]
    root_cause_summary: str
    evidence_trail: List[Dict[str, Any]]
    suggested_remediation: str


class ReproducibilityScoreRead(BaseModel):
    exact_match: bool
    normalized_match: bool
    content_agreement_ratio: float
    metadata_agreement_ratio: float
    environment_consistency_score: float
    composite_reproducibility_index: float
    verdict: ReproducibilityVerdict
    unexplained_differences_count: int


class BuildComparisonRead(BaseModel):
    id: str
    build_id_1: str
    build_id_2: str
    created_at: datetime
    mode: ComparisonMode
    exact_match: bool
    normalized_match: bool
    score: ReproducibilityScoreRead
    diff_findings: List[DiffFindingItem]
    diagnostics: List[DiagnosticFindingItem]
    model_config = ConfigDict(from_attributes=True)
