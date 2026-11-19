# ReproForge: Cross-Environment Software Build Reproducibility and Diagnostic Framework

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![Architecture](https://img.shields.io/badge/architecture-modular%20monolith-emerald.svg)](architecture.puml)
[![Test Suite](https://img.shields.io/badge/tests-68%20passed-brightgreen.svg)](tests/)
[![License: Academic](https://img.shields.io/badge/License-Academic%20Evaluation-lightgrey.svg)](LICENSE)

> An automated system for detecting, classifying, and diagnosing non-deterministic software builds across independent build environments.

---

## 1. Project Overview

Modern continuous integration and deployment pipelines operate under the assumption that identical source revisions and build instructions produce identical binary artifacts. In practice, unpinned dependencies, file timestamp variance, archive ordering non-determinism, environment variable leakage, and toolchain drift break bit-for-bit reproducibility.

**ReproForge** is an academic research and engineering framework developed as an individual final-year capstone project (B.Tech Computer Science and Engineering, DevOps Specialization, Semester VII, UPES). It is aligned with the academic course **CICD Pipeline and Security (CSDV4009P)**.

Rather than merely displaying binary diffs, ReproForge correlates observed environment telemetry with structural artifact discrepancies to deliver **explainable, confidence-scored diagnoses and actionable remediation instructions**.

---

## 2. Key Capabilities

- **Environment Fingerprinting Engine**: Structured, canonical capture of operating system, kernel, CPU architecture, container runtime, compiler toolchains, locale, and build-time environment variables (with automated secret redaction).
- **Dual-Mode Comparison Layer**:
  - **Mode A (Strict Comparison)**: Cryptographic bit-for-bit identity check ($S_{exact}$).
  - **Mode B (Normalized Comparison)**: Semantic comparison after deterministic normalization of timestamps, file modes, UID/GID, and entry ordering ($S_{norm}$).
- **Rule-Based Diagnostic Correlation Engine**: 11-category classification matrix identifying root causes (timestamp drift, toolchain variation, environment leakage, unpinned dependencies) with explicit confidence scoring (`HIGH`, `MEDIUM`, `LOW`, `INCONCLUSIVE`) and epistemic status (`OBSERVED`, `INFERRED`, `HYPOTHESIS`, `EXPERIMENTALLY_VALIDATED`).
- **Reproducibility Assessment & CRI**: Transparent primary multi-dimensional vector $\mathbf{R} = \langle S_{exact}, S_{norm}, R_{content}, R_{meta}, S_{env}, N_{unexplained} \rangle$, categorical verdicts (`BIT_FOR_BIT_REPRODUCIBLE`, `NORMALIZED_REPRODUCIBLE`, `PARTIALLY_REPRODUCIBLE`, `NON_REPRODUCIBLE`, `INCONCLUSIVE`), and secondary Composite Reproducibility Index (CRI).
- **Shared Application Services & Durable Persistence**: Unified service layer (`BuildService`, `ComparisonService`, `DiagnosticService`, `FingerprintService`, `RepositoryService`) with async SQLite WAL persistence (`PRAGMA journal_mode=WAL`, `busy_timeout=5000`), crash resilience, and post-crash orphan reconciliation.
- **Standalone CLI & Native Desktop Integration**: Autonomous CLI application with seamless transition to a native Rust desktop GUI (Slint/Iced).

---

## 3. System Architecture

The project architecture is structured into clear, decoupled layers:

```
[Presentation Layer]     Native Rust Desktop UI (Slint / Iced) & Standalone CLI
        │
[Shared Service Layer]   BuildService, ComparisonService, DiagnosticService, FingerprintService
        │
[Execution Layer]        Isolated Docker/BuildKit & Local Sandbox Runners, WorkspaceManager
        │
[Analysis Layer]         Dual-Mode Normalizer, DiffEngine, 11 Diagnostic Rules, CRI Scorer
        │
[Persistence Layer]      Async Repositories with Crash Reconciler & SQLite WAL Database
```

See [architecture.puml](architecture.puml) for the complete PlantUML component diagram.

---

## 4. Getting Started

### Prerequisites
- Python 3.11+ (Python 3.14 recommended)
- Docker Desktop with BuildKit enabled
- Git

### Installation
1. Clone the repository:
   ```powershell
   git clone https://github.com/pranav7404/ReproForge.git
   cd ReproForge
   ```

2. Create and activate the virtual environment:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

3. Install dependencies:
   ```powershell
   pip install -r requirements.txt
   ```

4. Apply database migrations:
   ```powershell
   alembic upgrade head
   ```

### Native Desktop Application (Slint UI)
The desktop client is located in `desktop/` and built with Slint v1.19.0.

1. Build and run in Hardware-Accelerated mode (default):
   ```powershell
   cargo run --release --manifest-path desktop/Cargo.toml
   ```

2. Run with Software Rasterizer Fallback (for environments without compatible GPUs or display drivers):
   ```powershell
   cargo run --release --manifest-path desktop/Cargo.toml -- --renderer=software
   # or via environment variable:
   $env:SLINT_BACKEND="software"
   .\desktop\target\release\reproforge_desktop.exe
   ```

3. Benchmark permanent desktop application footprint:
   ```powershell
   .\.venv\Scripts\python.exe desktop/benchmarks/run_desktop_bench.py
   ```

---

## 5. Running Tests

Execute the automated test suite with coverage reporting:
```powershell
.\.venv\Scripts\pytest.exe -v --cov=backend.app
```

---

## 6. Project Roadmap

- [x] **Phase 0: Research Validation & Technical Blueprint**
- [x] **Phase 1: Architecture & Project Foundation** (Core Models, Schemas, Alembic, Environment Fingerprinter)
- [x] **Phase 2: Build Execution Engine & CLI Prototype** (Git Ingestion, Build Detector, Isolated Sandbox, Artifact Collector, CLI)
- [x] **Phase 3: Artifact Analysis & Normalization Framework** (Multi-hasher, Unpacker, OCI Inspector, Dual-Mode Normalizer, Diff Engine)
- [x] **Phase 4: Diagnostic Engine & Reproducibility Scoring** (11 tightened rules, evidence bundle with epistemic status, 3-tier remediation, CRI & multi-dimensional verdict)
- [x] **Phase 5A: Shared Application Services & Durable Persistence** (Async Repositories, SQLite WAL Pragmas, Crash Recovery, Service Layer, CLI Refactor)
- [x] **Milestone B: Slint vs. Iced Minimal Prototype Benchmarking** (Empirical release benchmarks, WGPU vs Tiny-Skia, 50k scaling, first-frame presentation)
- [x] **Milestone C: Desktop Framework Selection & UI Shell Foundation** (Slint v1.19.0 permanent client, 5 interactive views, DPI-safe native window management, hardware & software fallback)
- [ ] **Milestone D: Local Structured JSONL IPC Daemon & Process Supervision**
- [ ] **Milestone E: Desktop Overview, Repository & Build Screens**
- [ ] **Milestone F: Comparison, Diagnostics & Causality Graph Screens**
- [ ] **Milestone G: Perturbation, Reproducibility Passport & Remediation UI**
- [ ] **Milestone H: Packaging, Verification & Academic Deliverables**

---

## 7. Academic Course Alignment (UPES CSDV4009P)

- **CO1 (CI/CD Toolchain, Version Control, Build Automation)**: Exact Git revision tracking and automated containerized build orchestration.
- **CO2 (Security Practices in CI/CD Pipelines)**: Software supply chain verification; distinguishing benign differences from malicious artifact tampering.
- **CO3 (Reliable CI/CD Workflows)**: Detecting and diagnosing flaky, non-deterministic builds with durable crash recovery.
- **CO4 (DevSecOps Principles)**: Shift-left reproducibility verification with automated remediation guidance.
