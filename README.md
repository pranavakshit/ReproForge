# ReproForge: Cross-Environment Software Build Reproducibility and Diagnostic Framework

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![Architecture](https://img.shields.io/badge/architecture-modular%20monolith-emerald.svg)](architecture.puml)
[![Test Suite](https://img.shields.io/badge/tests-pytest%20passing-brightgreen.svg)](tests/)
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
- **Rule-Based Diagnostic Correlation Engine**: 11-category classification matrix identifying root causes (timestamp drift, toolchain variation, environment leakage, unpinned dependencies) with explicit confidence scoring (`HIGH`, `MEDIUM`, `LOW`, `INCONCLUSIVE`).
- **Composite Reproducibility Index (CRI)**: Transparent mathematical scoring combining exact equality, normalized equivalence, file content agreement, and metadata consistency.
- **Isolated Build Execution**: Ephemeral containerized sandbox execution enforcing timeout and resource constraints.

---

## 3. System Architecture

The project architecture is structured into clear, decoupled layers:

```
[Presentation Layer]     React/TypeScript Dashboard & Headless CLI
        │
[API Layer]              FastAPI REST Service & OpenAPI Controllers
        │
[Orchestration Layer]    Build Scheduler & Environment Profile Manager
        │
[Execution Layer]        Isolated Docker/BuildKit Sandbox Runners
        │
[Analysis Layer]         Fingerprinter, Normalizer, DiffEngine, DiagnosticEngine, CRI Scorer
        │
[Persistence Layer]      PostgreSQL / Async SQLite with Alembic Migrations
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
- [ ] **Phase 2: Build Execution Engine & CLI Prototype**
- [ ] **Phase 3: Artifact Analysis & Normalization Framework**
- [ ] **Phase 4: Diagnostic Engine & Reproducibility Scoring**
- [ ] **Phase 5: FastAPI Backend & Persistence Layer**
- [ ] **Phase 6: Professional Web Dashboard**
- [ ] **Phase 7: Controlled Experimental Benchmark Suite**
- [ ] **Phase 8: Final Documentation, Viva Preparation & Academic Submission**

---

## 7. Academic Course Alignment (UPES CSDV4009P)

- **CO1 (CI/CD Toolchain, Version Control, Build Automation)**: Exact Git revision tracking and automated containerized build orchestration.
- **CO2 (Security Practices in CI/CD Pipelines)**: Software supply chain verification; distinguishing benign differences from malicious artifact tampering.
- **CO3 (Reliable CI/CD Workflows)**: Detecting and diagnosing flaky, non-deterministic builds.
- **CO4 (DevSecOps Principles)**: Shift-left reproducibility verification with automated remediation guidance.
