"""ReproForge Diagnostic Correlation Engine.

Coordinates multi-source evidence extraction and evaluates tightened diagnostic
classification rules to produce explainable, confidence-scored diagnoses.
"""

from typing import List, Optional
from backend.app.core.logging import get_logger
from backend.app.diagnostics.evidence import DiagnosticContext, EpistemicStatus
from backend.app.diagnostics.rules import (
    BaseDiagnosticRule,
    DiagnosticRuleResult,
    RuleBaseImageVariation,
    RuleDependencyVariation,
    RuleEnvironmentLeakage,
    RuleExternalInputVariation,
    RuleFilesystemOrdering,
    RuleGeneratedContent,
    RuleMetadataVariation,
    RulePlatformVariation,
    RuleTimestampNondeterminism,
    RuleToolchainVariation,
    RuleUnknownVariation,
)
from backend.app.schemas.entities import DiagnosticCategory, DiagnosticFindingItem

logger = get_logger("reproforge.diagnostics.engine")


class DiagnosticEngine:
    """Evaluates diagnostic context against deterministic classification rules."""

    def __init__(self) -> None:
        self.rules: List[BaseDiagnosticRule] = [
            RuleTimestampNondeterminism(),
            RuleFilesystemOrdering(),
            RuleMetadataVariation(),
            RuleEnvironmentLeakage(),
            RuleToolchainVariation(),
            RuleDependencyVariation(),
            RuleBaseImageVariation(),
            RulePlatformVariation(),
            RuleGeneratedContent(),
            RuleExternalInputVariation(),
        ]
        self.fallback_rule = RuleUnknownVariation()

    def diagnose(self, context: DiagnosticContext) -> List[DiagnosticFindingItem]:
        """Execute full evidence-correlation and diagnostic classification pipeline."""
        summary = context.comparison_summary
        if summary.exact_match:
            logger.info("Artifacts are bit-for-bit identical; no diagnostic findings required.")
            return []

        logger.info(f"Diagnosing {len(summary.findings)} discrepancies across artifacts...")
        matched_results: List[DiagnosticRuleResult] = []

        # Evaluate all specific diagnostic rules
        for rule in self.rules:
            try:
                result = rule.evaluate(context)
                if result is not None:
                    matched_results.append(result)
            except Exception as e:
                logger.error(f"Error evaluating rule {rule.category.value}: {e}")

        # Check for controlled perturbation validation upgrade
        if context.perturbation_metadata:
            meta = context.perturbation_metadata
            verified = meta.get("intervention_verified", False)
            repeatable = meta.get("repeatable", False)
            no_confounders = not any(r.evidence_bundle.has_confounders for r in matched_results)

            if verified and repeatable and no_confounders:
                for res in matched_results:
                    res.epistemic_status = EpistemicStatus.EXPERIMENTALLY_VALIDATED
                    for e in res.evidence_bundle.supporting_evidence:
                        e.epistemic_status = EpistemicStatus.EXPERIMENTALLY_VALIDATED
                    logger.info(f"Upgraded {res.category.value} to EXPERIMENTALLY_VALIDATED via perturbation.")

        # Mandatory Fallback: If no rule matched with actionable confidence, emit UNKNOWN_VARIATION
        if not matched_results:
            logger.warning("No specific diagnostic rule matched; invoking UNKNOWN_VARIATION fallback.")
            fallback_result = self.fallback_rule.evaluate(context)
            matched_results.append(fallback_result)

        return [r.to_diagnostic_finding_item() for r in matched_results]
