# ReproForge Research Foundations & Contributions

## 1. Research Questions
- **RQ1 (Equivalence Recovery):** Can dual-mode structural archive normalization (timestamp, UID/GID, permission, and entry ordering canonicalization) distinguish benign metadata drift from semantic content drift compared with raw SHA-256 hashing?
- **RQ2 (Epistemic Discipline in Root-Cause Analysis):** Can evidence-tagged diagnostic rules (`OBSERVED`, `INFERRED`, `HYPOTHESIS`, `EXPERIMENTALLY_VALIDATED`) with explicit confounder checks prevent overclaiming on unmodeled randomness (`INCONCLUSIVE` fallback)?
- **RQ3 (Experimental Causal Validation):** Can automated single-variable controlled perturbations (`SOURCE_DATE_EPOCH`, `TZ`, `LC_ALL`, `UMASK`, `PYTHONHASHSEED`) promote hypothesized root causes to experimentally validated causal links in a Build Drift Causality DAG?
- **RQ4 (Desktop Resource Economy):** Can a compiled Rust + Slint native desktop client deliver real-time streaming logs, interactive causality graphs, and large-scale execution tables with minimal idle CPU and memory footprint compared with heavyweight Chromium-based shells?

## 2. Core Research Contributions
1. **Multi-Dimensional Reproducibility Vector + Secondary CRI:** Replaces single-number scoring tricks with transparent vector evaluation (`exact_match`, `normalized_match`, `content_agreement_ratio`, `metadata_agreement_ratio`, `environment_consistency_score`) plus an interpretable weighted Composite Reproducibility Index (`CRI`).
2. **Epistemic Status Taxonomy & Safe Unknown Fallback:** Every diagnostic finding separates direct observations from hypotheses. Opaque entropy where no rule has causal evidence falls back deterministically to `UNKNOWN_VARIATION` / `ReproducibilityVerdict.INCONCLUSIVE` (`EXP-08` verified).
3. **Build Drift Causality Graph (`CausalityGraphService`):** Links `Repository -> Revision -> Build -> Fingerprint -> Diff -> DiagnosticRule -> Remediation` with per-edge epistemic confidence and Graphviz `.dot` interoperability.
4. **3-Tier Safety-Policy Remediation (`RemediationEngine`):** Enforces mechanical separation between Level 1 deterministic disposable repairs, Level 2 human-approved configuration patches, and Level 3 diagnosis-only boundaries.
5. **Dual-Backend Native Desktop Architecture:** Empirical measurement of the finalized `reproforge_desktop.exe` confirms `13.17 MB` binary size and `41.32 MB` idle RAM under the software rasterizer while retaining full hardware GPU acceleration (`FemtoVG`) and `N=50,000` row `ListView` virtualization.
