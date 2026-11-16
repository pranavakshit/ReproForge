"""ReproForge Diagnostic Evidence Model.

Models direct observations, provenance, supporting and contradicting evidence,
and the epistemic boundary between observed facts, rule inferences, and
experimentally validated relationships.
"""

import uuid
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from backend.app.comparison.diff import ArtifactComparisonSummary
from backend.app.execution.runner import BuildExecutionResult
from backend.app.orchestration.detector import DetectedBuildSystem
from backend.app.schemas.entities import EnvironmentFingerprintRead


class EpistemicStatus(str, Enum):
    """Epistemic classification of diagnostic claims."""

    OBSERVED = "observed"
    # Direct factual observation from telemetry, hashes, or diff findings.

    INFERRED = "inferred"
    # Logical deduction linking observations via rule constraints without physical intervention.

    HYPOTHESIS = "hypothesis"
    # Plausible candidate explanation with incomplete or circumstantial corroboration.

    EXPERIMENTALLY_VALIDATED = "experimentally_validated"
    # Confirmed by controlled single-variable intervention with verified execution,
    # repeatability across trials, and explicit absence of confounders.


class EvidenceType(str, Enum):
    """Categorized provenance types for diagnostic evidence."""

    DIFF_OBSERVATION = "diff_observation"
    ENV_VAR_CORRELATION = "env_var_correlation"
    ENV_VAR_BYTE_MATCH = "env_var_byte_match"
    TOOLCHAIN_VERSION_DELTA = "toolchain_version_delta"
    TOOLCHAIN_BYTECODE_MATCH = "toolchain_bytecode_match"
    DEPENDENCY_DECLARATION_UNPINNED = "dependency_declaration_unpinned"
    DEPENDENCY_LOG_VERSION_DELTA = "dependency_log_version_delta"
    CONTAINER_BASE_CONFIG_DELTA = "container_base_config_delta"
    CONTAINER_LAYER_DIFF_ID_DELTA = "container_layer_diff_id_delta"
    ARCHIVE_ORDERING_DELTA = "archive_ordering_delta"
    METADATA_PERMISSION_DELTA = "metadata_permission_delta"
    PLATFORM_ARCHITECTURE_DELTA = "platform_architecture_delta"
    DYNAMIC_TOKEN_MATCH = "dynamic_token_match"
    NETWORK_DOWNLOAD_DELTA = "network_download_delta"
    CONFOUNDING_OBSERVATION = "confounding_observation"


class EvidenceItem(BaseModel):
    """A single factual observation or structured deduction with complete provenance."""

    evidence_id: str = Field(default_factory=lambda: f"ev-{uuid.uuid4().hex[:8]}")
    evidence_type: EvidenceType
    epistemic_status: EpistemicStatus = EpistemicStatus.OBSERVED
    source_entity: str
    observed_fact: str
    raw_payload: Dict[str, Any] = Field(default_factory=dict)
    is_contradictory: bool = False


class EvidenceBundle(BaseModel):
    """Collection of supporting, contradicting, and confounding evidence items."""

    supporting_evidence: List[EvidenceItem] = Field(default_factory=list)
    contradicting_evidence: List[EvidenceItem] = Field(default_factory=list)
    confounding_factors: List[EvidenceItem] = Field(default_factory=list)

    def add_supporting(self, item: EvidenceItem) -> None:
        self.supporting_evidence.append(item)

    def add_contradicting(self, item: EvidenceItem) -> None:
        item.is_contradictory = True
        self.contradicting_evidence.append(item)

    def add_confounder(self, item: EvidenceItem) -> None:
        self.confounding_factors.append(item)

    @property
    def has_confounders(self) -> bool:
        return len(self.confounding_factors) > 0

    @property
    def has_contradictions(self) -> bool:
        return len(self.contradicting_evidence) > 0


class DiagnosticContext(BaseModel):
    """Aggregates all execution, environment, log, and comparison data for diagnosis."""

    comparison_summary: ArtifactComparisonSummary
    env_fingerprint_1: Optional[EnvironmentFingerprintRead] = None
    env_fingerprint_2: Optional[EnvironmentFingerprintRead] = None
    build_1_result: Optional[BuildExecutionResult] = None
    build_2_result: Optional[BuildExecutionResult] = None
    build_system: Optional[DetectedBuildSystem] = None
    build_1_log_text: Optional[str] = None
    build_2_log_text: Optional[str] = None
    perturbation_metadata: Optional[Dict[str, Any]] = None

    def get_changed_env_vars(self) -> Dict[str, Dict[str, str]]:
        """Identify environment variables that changed between build 1 and build 2."""
        if not self.env_fingerprint_1 or not self.env_fingerprint_2:
            return {}

        env1 = self.env_fingerprint_1.environment_variables
        env2 = self.env_fingerprint_2.environment_variables

        changed: Dict[str, Dict[str, str]] = {}
        all_keys = set(env1.keys()) | set(env2.keys())

        for k in all_keys:
            v1 = env1.get(k)
            v2 = env2.get(k)
            if v1 != v2:
                changed[k] = {"val_1": v1 or "<unset>", "val_2": v2 or "<unset>"}

        return changed

    def get_changed_toolchains(self) -> Dict[str, Dict[str, Any]]:
        """Identify toolchain versions that changed between build 1 and build 2."""
        if not self.env_fingerprint_1 or not self.env_fingerprint_2:
            return {}

        tools1 = self.env_fingerprint_1.toolchain_info.tools
        tools2 = self.env_fingerprint_2.toolchain_info.tools

        changed: Dict[str, Dict[str, Any]] = {}
        all_tools = set(tools1.keys()) | set(tools2.keys())

        for t in all_tools:
            info1 = tools1.get(t, {})
            info2 = tools2.get(t, {})
            v1 = info1.get("version")
            v2 = info2.get("version")
            if v1 != v2:
                changed[t] = {"tool_1": info1, "tool_2": info2}

        return changed
