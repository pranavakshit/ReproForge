# ReproForge

**Cross-Environment Software Build Reproducibility & Diagnostic Framework**

ReproForge is a local-first workbench for verifying, diagnosing, and remediating non-deterministic software builds across environments. It pairs a compiled **Rust + Slint** desktop studio (`13.17 MB` standalone binary, `41.32 MB` idle software RAM) with a concurrent Python async backend communicating over stdio JSON-RPC 2.0.

See [architecture.puml](architecture.puml) for the full system component diagram.

---

## Core Capabilities

- **Core Capabilities & Per-Repo Build Isolation:** Local builds automatically provision a dedicated `.venv` inside each build's exported workspace (`build_workspaces/<id>/source/.venv`) rooted at the base Python interpreter and stripped of ReproForge's own `VIRTUAL_ENV`, `PYTHONPATH`, and `PATH` entries, ensuring every target repo builds in its own isolated `.venv`.
- **Dual-Mode Artifact Comparison:** Strict SHA-256 verification alongside deterministic structural normalization across `.tar`, `.zip`, `.whl`, directory trees, and OCI/Docker container image manifests (`ArchiveUnpacker`, `ArtifactNormalizer`, `OCIImageInspector`).
- **Epistemic Root-Cause Diagnostics:** Evaluates discrepancies across 11 tightened causal rules (`timestamp_nondeterminism`, `filesystem_ordering`, `metadata_variation`, `dependency_variation`, `environment_leakage`, `toolchain_variation`, `base_image_variation`, `platform_variation`, `generated_content`, `external_input_variation`) with confounder checks and an explicit `UNKNOWN_VARIATION` / `INCONCLUSIVE` safety fallback.
- **Multi-Dimensional Reproducibility Vector + Secondary CRI:** Computes exact match, normalized match, content agreement, metadata agreement, environment consistency, and weighted Composite Reproducibility Index (`CRI`).
- **Local Folder & GitHub Autoscan:** Native OS folder picker (`rfd`), selectable checklist for multi-repo parent workspace folders, static build & CI detection (`Python`, `Node`, `Rust`, `Go`, `Docker`, `.github/workflows/*.yml`), and read-only non-destructive GitHub upstream inspection (`GitHubAutoScanner`).
- **Build Drift Causality Graph:** Synthesizes an interactive Directed Acyclic Graph (`Revision -> Build -> Environment -> Diff -> Diagnostic Rule -> Remediation`) with epistemic provenance badges and Graphviz `.dot` export.
- **Controlled Perturbation Lab:** Executes isolated single-variable perturbations (`SOURCE_DATE_EPOCH`, `TZ`, `LC_ALL`, `UMASK`, `PYTHONHASHSEED`) with confounder guards to empirically promote diagnostic hypotheses to `EXPERIMENTALLY_VALIDATED`.
- **Reproducibility Passport (`v1.0`):** Generates and exports canonical SHA-256–digested provenance records in JSON and Markdown with automatic credential/secret redaction.
- **3-Tier Safety Remediation Engine:** Generates unified diffs and executes Level 1 and explicitly approved Level 2 repairs inside isolated disposable copy workspaces (`build_workspaces/`) with dual-build SHA-256 verification, while blocking Level 3 architectural findings from automated patching.
- **Reporting & Optional Headless API:** Standalone JSON/CSV/HTML comparison reporting (`backend/app/reporting/`) and a thin optional FastAPI HTTP adapter (`backend/app/api/`) delegating to shared services.

---

## Repository Layout

```text
ReproForge/
├── backend/app/
│   ├── api/                 # Optional headless FastAPI REST adapter
│   ├── artifacts/           # Artifact collection & ingestion
│   ├── cli/                 # Click CLI (`reproforge`) & IPC daemon entrypoint
│   ├── comparison/          # Hashing, unpacking, OCI inspection & structural diffs
│   ├── core/                # Config settings & stderr-isolated Rich logging
│   ├── database/            # SQLAlchemy 2.0 async engine, WAL pragmas & sessions
│   ├── diagnostics/         # Evidence bundles, 11 classification rules & remediation templates
│   ├── execution/           # Workspace isolation, local sandbox & Docker build runner
│   ├── fingerprinting/      # OS, CPU, locale, toolchain & env capture
│   ├── ipc/                 # Concurrent stdio JSON-RPC 2.0 daemon & dispatcher
│   ├── models/              # ORM database schema
│   ├── normalization/       # Deterministic archive canonicalization + rule audit records
│   ├── orchestration/       # Git manager, build/CI detector, GitHubAutoScanner, seeders
│   ├── reporting/           # Standalone JSON, CSV, and HTML report exporter
│   ├── repositories/        # Data access layer
│   ├── schemas/             # Pydantic v2 contracts
│   ├── scoring/             # Vector verdict & CRI calculator
│   └── services/            # Shared services (Overview, Build, Comparison, Graph, Perturb, Passport, Remediation)
├── desktop/                 # Rust + Slint 1.19.0 Obsidian Studio & supervisor watchdog
├── alembic/                 # SQLite schema migrations
├── experiments/benchmarks/  # Section 10 10-scenario evaluation & outputs
├── scripts/                 # Portable launch scripts (Windows PowerShell & POSIX Bash)
├── tests/                   # 106 pytest tests + 3 Rust supervisor integration tests
├── start.ps1                # Root Windows PowerShell start script (Desktop GUI or -Api)
├── start.bat                # Root Windows CMD / double-click start script
├── start.sh                 # Root POSIX / Linux / macOS start script
├── reproforge.bat           # Alternate Windows Desktop Studio launcher
└── architecture.puml        # Full system architecture diagram
```

---

## Setup & Usage

### 1. Python Backend & Database
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
.\.venv\Scripts\alembic.exe upgrade head
```

### 2. Launch ReproForge Desktop Studio
- **Windows PowerShell (Root Start Script):**
  ```powershell
  .\start.ps1
  # low-RAM CPU software renderer (~41 MB idle RAM):
  .\start.ps1 -Renderer software
  # start optional headless FastAPI server instead:
  .\start.ps1 -Api
  ```
- **Windows CMD / Explorer:**
  ```cmd
  start.bat
  ```
- **Linux / macOS:**
  ```bash
  ./start.sh
  # or software renderer / headless API:
  ./start.sh software
  ./start.sh --api
  ```
- **Build release binary explicitly:**
  ```powershell
  cargo build --release --manifest-path desktop/Cargo.toml
  ```

### 3. Live End-to-End Verification Flow (After Adding a Repo)
1. Launch `.\start.ps1` and open **Repositories** (`Ctrl+2`).
2. Click **Browse Folder**, pick a local repo or workspace folder, select repository checkboxes, and click **Track & Autoscan Selected**.
3. Click **Verify Reproducibility (2x Build + Compare)** on the repository card (or in **Builds & Logs**, `Ctrl+3`): ReproForge runs Build A and Build B in separate isolated `.venv`s, collects built packages/archives, compares them, and automatically opens **Diff & Diagnostics** (`Ctrl+4`).
4. Explore **Causality Graph** (`Ctrl+5`), run **Perturbation Lab** (`Ctrl+6`) to validate hypotheses experimentally, export JSON/Markdown under **Passport & Audit** (`Ctrl+7`), and test disposable-workspace fixes under **Remediation** (`Ctrl+8`).

### 4. CLI, Wiki & Optional Headless API
```powershell
# CLI repository onboarding, scan & static GitHub autoscan
.\.venv\Scripts\reproforge.exe repo add .
.\.venv\Scripts\reproforge.exe repo scan D:\Repositories

# Full CLI reference & automation guide
# See docs/CLI_WIKI.md

# Optional headless FastAPI server (desktop does not depend on HTTP)
.\.venv\Scripts\uvicorn.exe backend.app.api.app:app --host 127.0.0.1 --port 8765
```

### 5. Project Documentation
- [CLI & Automation Wiki](docs/CLI_WIKI.md) — Full `reproforge` command reference, flags, launchers, config, and FastAPI endpoints
- [System Architecture](docs/ARCHITECTURE.md) — Layer separation, IPC protocol, and safety boundaries
- [Architecture Decision Log](docs/DECISIONS.md) — ADRs for Slint/Rust, stdio JSON-RPC, static GitHub scan, and remediation
- [Empirical Evaluation & Benchmarks](docs/EXPERIMENTS.md) — 10-scenario suite and desktop memory/latency profiling
- [Research Foundations](docs/RESEARCH.md) — Research questions and core technical contributions
- [Development Roadmap](docs/ROADMAP.md) — Completed milestones and future extensions

---

## Verification & Empirical Benchmarks

### Automated Test Suites
- **Python Suite:** `.\.venv\Scripts\pytest.exe -q` — `106 passed`
- **Desktop & IPC Supervisor Suite:** `cargo test --manifest-path desktop/Cargo.toml` — `3 passed` (Python discovery, live JSON-RPC handshake/ping, crash auto-restart)

### Section 10 Controlled Benchmark Suite (`experiments/benchmarks/repro_benchmark.py`)
Measured against raw SHA-256 and raw path diff baselines across 10 standardized reproducibility experiments:
- **Verdict Accuracy:** `10 / 10` (`100.0%`)
- **Diagnostic Category Accuracy:** `10 / 10` (`100.0%`)
- **Normalized Equivalence Recovery:** `3 / 3` (`100.0%` on `EXP-02`, `EXP-06`, `EXP-10`)
- **Safe Unknown Fallback (`EXP-08`):** `True` (`unknown_variation` -> `inconclusive`)
- **Mean End-to-End Diagnostic Latency:** `18.47 ms`

### Release Desktop Footprint (`desktop/benchmarks/run_desktop_bench.py`)
Measured on compiled `desktop/target/release/reproforge_desktop.exe`:
- **Release Binary Size:** `13.17 MB`
- **Software Mode (`N=200`):** `41.32 MB` idle RAM, `0.52%` CPU, `121.4 ms` first-frame
- **Hardware FemtoVG (`N=200`):** `117.43 MB` idle RAM, `2.6%` CPU, `673.6 ms` first-frame
- **Hardware Scaling (`N=50,000`):** `148.04 MB` idle RAM, `842.6 ms` first-frame

---

## Roadmap Status
- [x] Milestones A–H complete (Core Engine, Slint/Iced Benchmarks, Obsidian Shell, Stdio IPC, Real Multi-Repo Data, Diagnostics & Causality Graph, Perturbation Lab, Reproducibility Passport, 3-Tier Remediation, Reporting, Optional FastAPI, 10-Scenario Evaluation, Portable Packaging, Academic Deliverables)
