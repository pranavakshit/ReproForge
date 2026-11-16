"""Deterministic rule-based nondeterminism classification engine.

Implements tightened, explainable rules across 11 failure categories with
explicit trigger conditions, disqualifiers, confounder checks, and mandatory
fallback to UNKNOWN_VARIATION / INCONCLUSIVE when evidence is insufficient.
"""

import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel

from backend.app.diagnostics.evidence import (
    DiagnosticContext,
    EpistemicStatus,
    EvidenceBundle,
    EvidenceItem,
    EvidenceType,
)
from backend.app.schemas.entities import (
    ConfidenceLevel,
    DiagnosticCategory,
    DiagnosticFindingItem,
)


class DiagnosticRuleResult(BaseModel):
    """Result of evaluating a single diagnostic classification rule."""

    category: DiagnosticCategory
    confidence: ConfidenceLevel
    epistemic_status: EpistemicStatus
    affected_paths: List[str]
    root_cause_summary: str
    remediation_suggestion: str
    evidence_bundle: EvidenceBundle

    def to_diagnostic_finding_item(self) -> DiagnosticFindingItem:
        """Convert to standard API schema."""
        trail = [
            {
                "evidence_id": e.evidence_id,
                "type": e.evidence_type.value,
                "status": e.epistemic_status.value,
                "fact": e.observed_fact,
                "source": e.source_entity,
                "is_contradictory": e.is_contradictory,
            }
            for e in self.evidence_bundle.supporting_evidence + self.evidence_bundle.contradicting_evidence + self.evidence_bundle.confounding_factors
        ]
        return DiagnosticFindingItem(
            category=self.category,
            confidence=self.confidence,
            affected_paths=self.affected_paths,
            root_cause_summary=self.root_cause_summary,
            evidence_trail=trail,
            suggested_remediation=self.remediation_suggestion,
        )


class BaseDiagnosticRule:
    """Base class for all classification rules."""

    category: DiagnosticCategory

    def evaluate(self, ctx: DiagnosticContext) -> Optional[DiagnosticRuleResult]:
        raise NotImplementedError


# --- 1. TIMESTAMP NONDETERMINISM ---

class RuleTimestampNondeterminism(BaseDiagnosticRule):
    category = DiagnosticCategory.TIMESTAMP_NONDETERMINISM

    # Regex detecting ISO8601 or common build timestamp strings
    TIMESTAMP_REGEX = re.compile(
        r"\b\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b"
    )

    def evaluate(self, ctx: DiagnosticContext) -> Optional[DiagnosticRuleResult]:
        summary = ctx.comparison_summary
        if summary.exact_match:
            return None

        bundle = EvidenceBundle()
        affected_paths: List[str] = []

        # Check for Confounder: Did toolchain change?
        changed_tools = ctx.get_changed_toolchains()
        if changed_tools:
            bundle.add_confounder(
                EvidenceItem(
                    evidence_type=EvidenceType.CONFOUNDING_OBSERVATION,
                    epistemic_status=EpistemicStatus.OBSERVED,
                    source_entity="env_fingerprint:toolchains",
                    observed_fact=f"Toolchains diverged: {list(changed_tools.keys())}, which may confound timestamp causality.",
                )
            )

        # Case A: Pure metadata timestamp divergence (normalized match holds and content ratio is 1.0)
        metadata_only_diffs = [f for f in summary.findings if f.diff_type == "METADATA_ONLY"]
        mtime_diffs = [
            f for f in metadata_only_diffs
            if f.details.get("mtime_1") != f.details.get("mtime_2")
        ]

        if summary.content_agreement_ratio == 1.0 and len(mtime_diffs) > 0 and summary.normalized_match:
            affected_paths = [f.file_path for f in mtime_diffs]
            bundle.add_supporting(
                EvidenceItem(
                    evidence_type=EvidenceType.DIFF_OBSERVATION,
                    epistemic_status=EpistemicStatus.OBSERVED,
                    source_entity="artifact_comparison",
                    observed_fact=f"All file contents match (R_content=1.0), but {len(mtime_diffs)} file(s) have divergent modification timestamps.",
                    raw_payload={"sample_divergence": mtime_diffs[0].details},
                )
            )

            # High confidence if no confounding toolchain changes
            confidence = ConfidenceLevel.HIGH if not bundle.has_confounders else ConfidenceLevel.MEDIUM
            status = EpistemicStatus.INFERRED

            return DiagnosticRuleResult(
                category=self.category,
                confidence=confidence,
                epistemic_status=status,
                affected_paths=affected_paths,
                root_cause_summary="Archive entry modification timestamps reflect build execution time rather than deterministic epoch.",
                remediation_suggestion="Define and export SOURCE_DATE_EPOCH in build instructions, or clamp archive mtime.",
                evidence_bundle=bundle,
            )

        # Case B: Localized timestamp string embedded in file content
        content_diffs = [f for f in summary.findings if f.diff_type == "MODIFIED_CONTENT"]
        embedded_ts_paths = []

        for cd in content_diffs:
            snippet = str(cd.details.get("diff_snippet", "")) + str(cd.details)
            if self.TIMESTAMP_REGEX.search(snippet):
                embedded_ts_paths.append(cd.file_path)

        if embedded_ts_paths and len(embedded_ts_paths) == len(content_diffs):
            bundle.add_supporting(
                EvidenceItem(
                    evidence_type=EvidenceType.DIFF_OBSERVATION,
                    epistemic_status=EpistemicStatus.OBSERVED,
                    source_entity="artifact_content",
                    observed_fact=f"Timestamp string pattern found in modified content of: {embedded_ts_paths}",
                )
            )
            confidence = ConfidenceLevel.MEDIUM if not bundle.has_confounders else ConfidenceLevel.LOW
            return DiagnosticRuleResult(
                category=self.category,
                confidence=confidence,
                epistemic_status=EpistemicStatus.HYPOTHESIS,
                affected_paths=embedded_ts_paths,
                root_cause_summary="Build appears to generate or embed a dynamic wall-clock timestamp string into output files.",
                remediation_suggestion="Inspect build scripts for 'date', 'datetime.now()', or build banner generators.",
                evidence_bundle=bundle,
            )

        return None


# --- 2. DEPENDENCY VARIATION ---

class RuleDependencyVariation(BaseDiagnosticRule):
    category = DiagnosticCategory.DEPENDENCY_VARIATION

    def evaluate(self, ctx: DiagnosticContext) -> Optional[DiagnosticRuleResult]:
        summary = ctx.comparison_summary
        if summary.exact_match:
            return None

        bundle = EvidenceBundle()
        affected_paths: List[str] = []

        # Find diffs in third-party dependency paths
        dep_diffs = [
            f for f in summary.findings
            if any(p in f.file_path.lower() for p in ["site-packages", "node_modules", "vendor", "wheels"])
        ]

        # Check for unpinned lockfile
        has_lockfile = ctx.build_system.has_lockfile if ctx.build_system else True
        if not has_lockfile:
            bundle.add_supporting(
                EvidenceItem(
                    evidence_type=EvidenceType.DEPENDENCY_DECLARATION_UNPINNED,
                    epistemic_status=EpistemicStatus.OBSERVED,
                    source_entity="build_configuration",
                    observed_fact="Build configuration does not specify a lockfile with pinned cryptographic hashes.",
                )
            )

        # Inspect build logs for explicit package version divergence
        log1 = ctx.build_1_log_text or ""
        log2 = ctx.build_2_log_text or ""
        explicit_log_divergence = False

        if log1 and log2:
            # Look for lines like "Installing package-1.2.3" vs "Installing package-1.2.4"
            pkg_pattern = re.compile(r"(?:Installing|Collecting|downloading)\s+([a-zA-Z0-9_\-\.]+)[= -]([0-9][a-zA-Z0-9_\-\.]*)", re.I)
            pkgs1 = dict(pkg_pattern.findall(log1))
            pkgs2 = dict(pkg_pattern.findall(log2))

            for name, v1 in pkgs1.items():
                if name in pkgs2 and pkgs2[name] != v1:
                    explicit_log_divergence = True
                    bundle.add_supporting(
                        EvidenceItem(
                            evidence_type=EvidenceType.DEPENDENCY_LOG_VERSION_DELTA,
                            epistemic_status=EpistemicStatus.OBSERVED,
                            source_entity="build_logs",
                            observed_fact=f"Dependency '{name}' resolved version {v1} in build 1 vs {pkgs2[name]} in build 2.",
                        )
                    )

        if dep_diffs:
            affected_paths = [f.file_path for f in dep_diffs]
            bundle.add_supporting(
                EvidenceItem(
                    evidence_type=EvidenceType.DIFF_OBSERVATION,
                    epistemic_status=EpistemicStatus.OBSERVED,
                    source_entity="artifact_diff",
                    observed_fact=f"Detected {len(dep_diffs)} divergent files in dependency directories (site-packages/node_modules).",
                )
            )

        # Missing lockfile ALONE is not sufficient proof! We require log proof or dependency diffs
        if explicit_log_divergence:
            return DiagnosticRuleResult(
                category=self.category,
                confidence=ConfidenceLevel.HIGH,
                epistemic_status=EpistemicStatus.INFERRED,
                affected_paths=affected_paths,
                root_cause_summary="Third-party package dependency versions diverged between build executions.",
                remediation_suggestion="Generate and commit a strict dependency lockfile (e.g. poetry.lock, package-lock.json).",
                evidence_bundle=bundle,
            )
        elif dep_diffs and not has_lockfile:
            return DiagnosticRuleResult(
                category=self.category,
                confidence=ConfidenceLevel.MEDIUM,
                epistemic_status=EpistemicStatus.HYPOTHESIS,
                affected_paths=affected_paths,
                root_cause_summary="Dependency files differ and no lockfile was detected, suggesting dependency drift.",
                remediation_suggestion="Pin all direct and transitive dependencies to immutable versions and hashes.",
                evidence_bundle=bundle,
            )

        return None


# --- 3. ENVIRONMENT LEAKAGE ---

class RuleEnvironmentLeakage(BaseDiagnosticRule):
    category = DiagnosticCategory.ENVIRONMENT_LEAKAGE

    def evaluate(self, ctx: DiagnosticContext) -> Optional[DiagnosticRuleResult]:
        summary = ctx.comparison_summary
        if summary.exact_match:
            return None

        changed_vars = ctx.get_changed_env_vars()
        if not changed_vars:
            return None

        bundle = EvidenceBundle()
        affected_paths: List[str] = []
        exact_match_found = False

        # Search for exact value of changed environment variable in modified files
        for f in summary.findings:
            if f.diff_type in ["MODIFIED_CONTENT", "ADDED"]:
                snippet = str(f.details.get("diff_snippet", "")) + str(f.details)
                for var_name, vals in changed_vars.items():
                    val1 = vals["val_1"]
                    val2 = vals["val_2"]

                    # Ignore trivial or very short strings (< 4 chars) to prevent false substring matches
                    if len(val1) >= 4 and val1 in snippet:
                        exact_match_found = True
                        affected_paths.append(f.file_path)
                        bundle.add_supporting(
                            EvidenceItem(
                                evidence_type=EvidenceType.ENV_VAR_BYTE_MATCH,
                                epistemic_status=EpistemicStatus.OBSERVED,
                                source_entity=f"artifact:{f.file_path}",
                                observed_fact=f"Environment variable ${var_name} value '{val1}' embedded in {f.file_path}",
                            )
                        )
                    elif len(val2) >= 4 and val2 in snippet:
                        exact_match_found = True
                        affected_paths.append(f.file_path)
                        bundle.add_supporting(
                            EvidenceItem(
                                evidence_type=EvidenceType.ENV_VAR_BYTE_MATCH,
                                epistemic_status=EpistemicStatus.OBSERVED,
                                source_entity=f"artifact:{f.file_path}",
                                observed_fact=f"Environment variable ${var_name} value '{val2}' embedded in {f.file_path}",
                            )
                        )

        # Crucial check: Coincidental env var difference alone does NOT establish causality!
        if exact_match_found:
            return DiagnosticRuleResult(
                category=self.category,
                confidence=ConfidenceLevel.HIGH,
                epistemic_status=EpistemicStatus.INFERRED,
                affected_paths=list(set(affected_paths)),
                root_cause_summary="Host environment variable values leaked into compiled build artifacts.",
                remediation_suggestion="Sanitize build environment using an explicit allowlist or 'env -i' wrapper.",
                evidence_bundle=bundle,
            )

        return None


# --- 4. TOOLCHAIN VARIATION ---

class RuleToolchainVariation(BaseDiagnosticRule):
    category = DiagnosticCategory.TOOLCHAIN_VARIATION

    COMPILED_EXTENSIONS = (".pyc", ".o", ".so", ".a", ".class", ".dll", ".dylib", ".bin")

    def evaluate(self, ctx: DiagnosticContext) -> Optional[DiagnosticRuleResult]:
        summary = ctx.comparison_summary
        if summary.exact_match:
            return None

        changed_tools = ctx.get_changed_toolchains()
        if not changed_tools:
            return None

        bundle = EvidenceBundle()
        for t, vals in changed_tools.items():
            bundle.add_supporting(
                EvidenceItem(
                    evidence_type=EvidenceType.TOOLCHAIN_VERSION_DELTA,
                    epistemic_status=EpistemicStatus.OBSERVED,
                    source_entity="environment_fingerprint",
                    observed_fact=f"Toolchain '{t}' version differed: {vals['tool_1'].get('version')} vs {vals['tool_2'].get('version')}",
                )
            )

        # Check if affected files are compiled binaries or bytecode
        compiled_diffs = [
            f for f in summary.findings
            if any(f.file_path.endswith(ext) for ext in self.COMPILED_EXTENSIONS)
        ]

        if compiled_diffs:
            affected = [f.file_path for f in compiled_diffs]
            bundle.add_supporting(
                EvidenceItem(
                    evidence_type=EvidenceType.TOOLCHAIN_BYTECODE_MATCH,
                    epistemic_status=EpistemicStatus.OBSERVED,
                    source_entity="artifact_diff",
                    observed_fact=f"{len(compiled_diffs)} compiled binary/bytecode file(s) diverged.",
                )
            )
            return DiagnosticRuleResult(
                category=self.category,
                confidence=ConfidenceLevel.HIGH,
                epistemic_status=EpistemicStatus.INFERRED,
                affected_paths=affected,
                root_cause_summary=f"Compiler or runtime version divergence ({', '.join(changed_tools.keys())}) produced differing bytecode or binaries.",
                remediation_suggestion="Standardize compiler and runtime versions across build environments or use pinned container base images.",
                evidence_bundle=bundle,
            )

        # If toolchain differed but only source/doc files changed, toolchain did NOT cause it!
        bundle.add_contradicting(
            EvidenceItem(
                evidence_type=EvidenceType.CONFOUNDING_OBSERVATION,
                epistemic_status=EpistemicStatus.OBSERVED,
                source_entity="artifact_diff",
                observed_fact="Toolchains diverged, but none of the modified files are compiled binaries or bytecode.",
            )
        )
        return None


# --- 5. FILESYSTEM ORDERING ---

class RuleFilesystemOrdering(BaseDiagnosticRule):
    category = DiagnosticCategory.FILESYSTEM_ORDERING

    def evaluate(self, ctx: DiagnosticContext) -> Optional[DiagnosticRuleResult]:
        summary = ctx.comparison_summary
        if summary.exact_match:
            return None

        # Content must be 100% identical, normalized match holds, but raw hashes differ
        if summary.content_agreement_ratio == 1.0 and summary.normalized_match and not summary.exact_match:
            # Check if there are no modified content findings
            content_mods = [f for f in summary.findings if f.diff_type == "MODIFIED_CONTENT"]
            if not content_mods and summary.total_files_evaluated > 1:
                bundle = EvidenceBundle()
                bundle.add_supporting(
                    EvidenceItem(
                        evidence_type=EvidenceType.ARCHIVE_ORDERING_DELTA,
                        epistemic_status=EpistemicStatus.OBSERVED,
                        source_entity="archive_structure",
                        observed_fact=f"All {summary.total_files_evaluated} files have identical content, but archive hash diverged due to member entry ordering.",
                    )
                )
                return DiagnosticRuleResult(
                    category=self.category,
                    confidence=ConfidenceLevel.HIGH,
                    epistemic_status=EpistemicStatus.INFERRED,
                    affected_paths=[summary.artifact_1_path, summary.artifact_2_path],
                    root_cause_summary="Archive entries were packed in non-deterministic directory traversal (readdir) order.",
                    remediation_suggestion="Enforce lexicographical entry ordering before archiving (e.g. tar --sort=name).",
                    evidence_bundle=bundle,
                )

        return None


# --- 6. METADATA VARIATION ---

class RuleMetadataVariation(BaseDiagnosticRule):
    category = DiagnosticCategory.METADATA_VARIATION

    def evaluate(self, ctx: DiagnosticContext) -> Optional[DiagnosticRuleResult]:
        summary = ctx.comparison_summary
        if summary.exact_match:
            return None

        # Content must match 100%, and differences must be permission modes or UID/GID
        if summary.content_agreement_ratio == 1.0:
            perm_diffs = [
                f for f in summary.findings
                if f.diff_type == "METADATA_ONLY" and f.details.get("mode_1") != f.details.get("mode_2")
            ]

            if perm_diffs:
                bundle = EvidenceBundle()
                bundle.add_supporting(
                    EvidenceItem(
                        evidence_type=EvidenceType.METADATA_PERMISSION_DELTA,
                        epistemic_status=EpistemicStatus.OBSERVED,
                        source_entity="filesystem_metadata",
                        observed_fact=f"{len(perm_diffs)} file(s) have identical content but divergent POSIX permission modes (umask effect).",
                        raw_payload={"sample_mode_diff": perm_diffs[0].details},
                    )
                )
                return DiagnosticRuleResult(
                    category=self.category,
                    confidence=ConfidenceLevel.HIGH,
                    epistemic_status=EpistemicStatus.INFERRED,
                    affected_paths=[f.file_path for f in perm_diffs],
                    root_cause_summary="File permission modes or ownership attributes diverged due to differing build umask or user accounts.",
                    remediation_suggestion="Clamp file permissions to 0644/0755 and enforce standard umask 0022 during build.",
                    evidence_bundle=bundle,
                )

        return None


# --- 7. BASE IMAGE VARIATION ---

class RuleBaseImageVariation(BaseDiagnosticRule):
    category = DiagnosticCategory.BASE_IMAGE_VARIATION

    def evaluate(self, ctx: DiagnosticContext) -> Optional[DiagnosticRuleResult]:
        summary = ctx.comparison_summary
        if summary.exact_match:
            return None

        # Only relevant for container image comparisons
        is_container = any(
            f.file_path.endswith((".tar", ".oci")) or "docker_oci_image" in str(f.details)
            for f in summary.findings
        )
        if not is_container and "oci" not in summary.artifact_1_path.lower():
            return None

        bundle = EvidenceBundle()

        # Check if Docker build system used unpinned mutable base tag
        if ctx.build_system and ctx.build_system.system_type == "docker":
            unpinned_images = [img for img in ctx.build_system.base_images if "@sha256:" not in img]
            if unpinned_images:
                bundle.add_supporting(
                    EvidenceItem(
                        evidence_type=EvidenceType.CONTAINER_BASE_CONFIG_DELTA,
                        epistemic_status=EpistemicStatus.OBSERVED,
                        source_entity="Dockerfile",
                        observed_fact=f"Base image reference(s) {unpinned_images} use mutable tags rather than immutable digests.",
                    )
                )
                return DiagnosticRuleResult(
                    category=self.category,
                    confidence=ConfidenceLevel.MEDIUM,
                    epistemic_status=EpistemicStatus.HYPOTHESIS,
                    affected_paths=[summary.artifact_1_path, summary.artifact_2_path],
                    root_cause_summary="Container build relies on a mutable base image tag which may have updated upstream.",
                    remediation_suggestion="Pin base container images to immutable SHA-256 digests (FROM image@sha256:...).",
                    evidence_bundle=bundle,
                )

        return None


# --- 8. GENERATED CONTENT ---

class RuleGeneratedContent(BaseDiagnosticRule):
    category = DiagnosticCategory.GENERATED_CONTENT

    UUID_REGEX = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
    HEX_NONCE_REGEX = re.compile(r"\b(?:nonce|token|session|build_id|uuid)[\s:=]+['\"]?([0-9a-fA-F]{16,64})['\"]?", re.I)

    def evaluate(self, ctx: DiagnosticContext) -> Optional[DiagnosticRuleResult]:
        summary = ctx.comparison_summary
        if summary.exact_match:
            return None

        bundle = EvidenceBundle()
        matched_paths = []

        for f in summary.findings:
            if f.diff_type in ["MODIFIED_CONTENT", "ADDED"]:
                snippet = str(f.details.get("diff_snippet", "")) + str(f.details)
                if self.UUID_REGEX.search(snippet) or self.HEX_NONCE_REGEX.search(snippet):
                    matched_paths.append(f.file_path)

        if matched_paths:
            bundle.add_supporting(
                EvidenceItem(
                    evidence_type=EvidenceType.DYNAMIC_TOKEN_MATCH,
                    epistemic_status=EpistemicStatus.OBSERVED,
                    source_entity="artifact_content",
                    observed_fact=f"Random UUID or nonce token detected in: {matched_paths}",
                )
            )
            return DiagnosticRuleResult(
                category=self.category,
                confidence=ConfidenceLevel.MEDIUM,
                epistemic_status=EpistemicStatus.HYPOTHESIS,
                affected_paths=matched_paths,
                root_cause_summary="Build appears to generate dynamic random tokens, UUIDs, or nonces into generated artifacts.",
                remediation_suggestion="Ensure build identifiers and secret tokens are deterministic or injected during runtime rather than build time.",
                evidence_bundle=bundle,
            )

        return None


# --- 9. PLATFORM VARIATION ---

class RulePlatformVariation(BaseDiagnosticRule):
    category = DiagnosticCategory.PLATFORM_VARIATION

    def evaluate(self, ctx: DiagnosticContext) -> Optional[DiagnosticRuleResult]:
        summary = ctx.comparison_summary
        if summary.exact_match:
            return None

        if not ctx.env_fingerprint_1 or not ctx.env_fingerprint_2:
            return None

        sys1 = ctx.env_fingerprint_1.system_info
        sys2 = ctx.env_fingerprint_2.system_info

        arch_diverged = sys1.cpu_arch != sys2.cpu_arch
        os_diverged = sys1.os_name != sys2.os_name

        if arch_diverged or os_diverged:
            bundle = EvidenceBundle()
            bundle.add_supporting(
                EvidenceItem(
                    evidence_type=EvidenceType.PLATFORM_ARCHITECTURE_DELTA,
                    epistemic_status=EpistemicStatus.OBSERVED,
                    source_entity="environment_fingerprint",
                    observed_fact=f"Platform environment diverged: OS ({sys1.os_name} vs {sys2.os_name}), Arch ({sys1.cpu_arch} vs {sys2.cpu_arch})",
                )
            )

            # Check if affected files reflect platform differences (binaries, wheels with arch tags)
            has_arch_files = any(
                any(arch in f.file_path.lower() for arch in ["x86_64", "amd64", "aarch64", "arm64", "win", "linux"])
                for f in summary.findings
            )

            confidence = ConfidenceLevel.HIGH if has_arch_files else ConfidenceLevel.MEDIUM
            return DiagnosticRuleResult(
                category=self.category,
                confidence=confidence,
                epistemic_status=EpistemicStatus.INFERRED,
                affected_paths=[f.file_path for f in summary.findings[:5]],
                root_cause_summary=f"Legitimate platform and CPU architecture divergence ({sys1.cpu_arch} vs {sys2.cpu_arch}) produced expected platform-specific outputs.",
                remediation_suggestion="Use multi-arch build environments or cross-compilation toolchains with explicit architecture target flags.",
                evidence_bundle=bundle,
            )

        return None


# --- 10. EXTERNAL INPUT VARIATION ---

class RuleExternalInputVariation(BaseDiagnosticRule):
    category = DiagnosticCategory.EXTERNAL_INPUT_VARIATION

    def evaluate(self, ctx: DiagnosticContext) -> Optional[DiagnosticRuleResult]:
        summary = ctx.comparison_summary
        if summary.exact_match:
            return None

        log1 = ctx.build_1_log_text or ""
        log2 = ctx.build_2_log_text or ""
        if not log1 and not log2:
            return None

        network_pattern = re.compile(r"(?:curl|wget|git clone|fetch|http[s]?://)\s+([^\s]+)", re.I)
        net1 = set(network_pattern.findall(log1))
        net2 = set(network_pattern.findall(log2))

        if net1 or net2:
            bundle = EvidenceBundle()
            bundle.add_supporting(
                EvidenceItem(
                    evidence_type=EvidenceType.NETWORK_DOWNLOAD_DELTA,
                    epistemic_status=EpistemicStatus.OBSERVED,
                    source_entity="build_logs",
                    observed_fact=f"Build logs record un-isolated network access ({len(net1 | net2)} network endpoints detected).",
                )
            )
            return DiagnosticRuleResult(
                category=self.category,
                confidence=ConfidenceLevel.MEDIUM,
                epistemic_status=EpistemicStatus.HYPOTHESIS,
                affected_paths=[summary.artifact_1_path, summary.artifact_2_path],
                root_cause_summary="Build script performed external network downloads, introducing potential mutable resource drift.",
                remediation_suggestion="Vend external resources, vendor dependencies locally, or configure build container with network isolation.",
                evidence_bundle=bundle,
            )

        return None


# --- 11. UNKNOWN VARIATION (MANDATORY FALLBACK) ---

class RuleUnknownVariation(BaseDiagnosticRule):
    category = DiagnosticCategory.UNKNOWN_VARIATION

    def evaluate(self, ctx: DiagnosticContext) -> DiagnosticRuleResult:
        summary = ctx.comparison_summary
        bundle = EvidenceBundle()
        bundle.add_supporting(
            EvidenceItem(
                evidence_type=EvidenceType.DIFF_OBSERVATION,
                epistemic_status=EpistemicStatus.OBSERVED,
                source_entity="artifact_comparison",
                observed_fact=f"Observed {len(summary.findings)} difference(s), but evidence is insufficient to prove any known root cause.",
            )
        )
        return DiagnosticRuleResult(
            category=self.category,
            confidence=ConfidenceLevel.INCONCLUSIVE,
            epistemic_status=EpistemicStatus.HYPOTHESIS,
            affected_paths=[f.file_path for f in summary.findings[:10]],
            root_cause_summary="Differences observed between artifacts without sufficient environmental or log correlation to establish root cause.",
            remediation_suggestion="Conduct in-depth binary analysis using diffoscope or perform controlled single-variable perturbation.",
            evidence_bundle=bundle,
        )
