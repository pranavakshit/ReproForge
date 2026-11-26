# ReproForge System Architecture

## 1. Architectural Vision & Layer Separation
ReproForge is architected as a decoupled, dual-runtime software build reproducibility and drift diagnostic workbench:

1. **Native Desktop Frontend (`desktop/`)**: Compiled Rust + Slint (`1.19.0`) GUI workstation (`13.17 MB` standalone release binary) with Obsidian vector theming, `ListView` row virtualization (`N=50,000+` scaling), hardware (`FemtoVG`) and CPU software (`winit-software`) rendering, native OS folder dialog (`rfd`), and a multi-threaded `ProcessSupervisor` watchdog.
2. **Local JSON-RPC 2.0 Transport (`backend/app/ipc/`)**: Newline-delimited JSON-RPC over `stdin`/`stdout` with concurrent non-blocking `asyncio.create_task` dispatch (`IpcDaemon`), thread-safe stdout serialization, `Rich` log isolation onto `stderr`, and real-time notification push (`build.progress`, `build.log_chunk`, `build.completed`).
3. **Shared Application Services (`backend/app/services/`)**: Core business logic reused uniformly across CLI, Desktop IPC, and the optional headless HTTP API:
   - `OverviewService`: Live host telemetry probe & aggregated verdict metrics
   - `RepositoryService`: Local/GitHub tracking, batch parent-folder discovery, and static autoscan
   - `BuildService`: Isolated build orchestration, live process registry, and clean cancellation
   - `ComparisonService`: Archive unpacking, dual-mode hashing/normalization, and contextual RCA wiring
   - `CausalityGraphService`: Directed Acyclic Graph synthesis & Graphviz `.dot` export
   - `PerturbationService`: Single-variable delta experiments with strict confounder guards
   - `PassportService`: Secret-redacted, canonical SHA-256 signed `reproforge-passport-v1.0.json` exports
   - `RemediationEngine`: 3-tier safety-gated repair inside isolated disposable workspaces
4. **Core Analysis & Execution Subsystems (`backend/app/`)**:
   - `orchestration/`: `GitRepositoryManager`, `BuildConfigurationDetector`, `GitHubAutoScanner`
   - `fingerprinting/`: Host OS, toolchain, locale/umask, and environment capture
   - `execution/`: `WorkspaceManager`, `LocalSandboxBackend` (allowlisted env + clean `git archive`), `DockerBuildBackend` (`--network=none`, `--memory=2g`, `--cpus=2`)
   - `artifacts/` & `normalization/` & `comparison/`: `ArtifactCollector`, `ArtifactHasher`, `ArchiveUnpacker`, `OCIImageInspector`, `ArtifactNormalizer`, `StructuralDiffEngine`
   - `diagnostics/` & `scoring/`: `EvidenceBundle` + `EpistemicStatus`, 11 tightened classification rules + mandatory `UNKNOWN_VARIATION` fallback, `ReproducibilityCalculator` (multi-dimensional vector + secondary CRI)
   - `reporting/` & `api/`: `ReportExporter` (JSON/CSV/HTML) and thin optional FastAPI headless adapter
5. **Persistence Layer (`backend/app/database/`, `models/`, `repositories/`)**: SQLite (`aiosqlite`) with WAL mode, strict foreign-key integrity, and Alembic schema migrations (`da5121aa74fd` -> `e7b2c19a0411`).

## 2. Safety & Process Isolation Boundaries
- **Static GitHub Autoscan**: Reads `.git/config`, runs `git ls-remote`, parses `.github/workflows/*.yml`, and queries read-only public GitHub metadata without ever executing arbitrary repository scripts on the host.
- **Process Supervision**: `IpcDaemon` redirects subprocess `stdin` to `DEVNULL` so child commands (`git`, `docker`, build runners) never contend with stdio JSON-RPC framing on Windows or POSIX.
- **Disposable Remediation**: Level 1 and approved Level 2 repairs execute strictly inside temporary copy workspaces (`build_workspaces/_repair_*`) and run independent dual-pass SHA-256 verification before marking any fix `VERIFIED`.
