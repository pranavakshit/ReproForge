# ReproForge Development Roadmap

## Completed Milestones
- **Phase 0–5A:** Research blueprint, SQLAlchemy 2.0/WAL persistence, Alembic migrations, environment fingerprinting, isolated build execution, multi-format normalization, 11 RCA diagnostic rules, CRI scoring, shared services, and Click CLI (`reproforge`).
- **Milestone B & C:** Slint vs Iced empirical benchmark, Obsidian UI shell foundation, hardware + software renderer fallback, search filtering, keyboard shortcuts.
- **Milestone D & E:** Local stdio JSON-RPC 2.0 IPC, concurrent async dispatcher, crash-resilient Rust watchdog, real multi-repo seeding (`ReproForge`, `LocAi`, `offline-doc-assistant`, `4-Zone-Keyboard-RGB-Toolkit`, `ssheasy`), live build streaming & OS cancellation.
- **Milestone F:** Side-by-side comparison inspector with byte/metadata diff snippets, CRI metric vector bars, epistemic evidence trail (`OBSERVED`, `INFERRED`, `HYPOTHESIS`, `EXPERIMENTALLY_VALIDATED`), and layered Build Drift Causality Graph (`.dot` export).
- **Phase 2 Improvements (UI & GitHub Autoscan):** Sleek vector-icon Obsidian design system, zero Slint warnings, native OS folder picker (`rfd`), parent workspace selectable checklist, non-destructive `GitHubAutoScanner`, Rust/Go/Workflow detection, §23.6 normalization audit records, sandbox env allowlisting & Docker container resource limits.
- **Milestone G:** Controlled `PerturbationService` with confounder guard & epistemic promotion, secret-redacted `PassportService` (`reproforge-passport-v1.0.json` + Markdown), and 3-tier `RemediationEngine` with disposable workspace verification rebuilds.
- **Milestone H:** `ReportExporter` (JSON/CSV/HTML), thin optional `FastAPI` headless adapter, 10-scenario controlled benchmark suite (`10/10` verdict & category accuracy), portable Windows/POSIX launchers, empirical desktop release profiling, and CLI/architecture documentation.
- **UI Diagnostics, Docker Wheelhouse & CLI Parity Hardening:** True isolated `DockerBuildBackend` execution without host `.venv` creation (`--network=none` offline cached wheelhouse default + `Source-Only` toggle, `docker logs` + `docker inspect` capture prior to cleanup), latest-first build history & per-`build_id` log stream isolation (`Follow Live Build`), enriched Causality Graph (`EVIDENCED_BY` proof links + node-incident edge filtering), interactive Perturbation Lab (`Asia/Kolkata` preset, inline enable switches, editable parameters, Full Builder toggle), and full CLI-to-UI parity (custom build command/timeout, arbitrary path comparison, HTML/CSV/JSON report exports, `detect` metadata, live Host Fingerprint Inspector).

## Potential Future Extensions
- Cross-host distributed worker agents comparing Linux/macOS/Windows builds over mTLS.
- SLSA v1.0 attestation envelope signing via hardware security keys (`sigstore` integration).
- Compiler plugin AST tracing for deep nondeterministic codegen localization.
