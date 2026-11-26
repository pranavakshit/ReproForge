# Architecture & Engineering Decision Log

1. **ADR-001 — Slint + Rust Native Shell over Electron/Browser UI**
   - **Decision:** Build `desktop/` in Rust with `slint = "1.19.0"` (`femtovg` + `software` renderers).
   - **Rationale:** Electron baselines typically consume 250–400 MB RSS at idle. Compiled Slint achieved `41.32 MB` software RAM, `13.17 MB` single executable footprint, and `121.4 ms` cold software startup while scaling `N=50,000` list rows smoothly via `ListView`.

2. **ADR-002 — Stdio JSON-RPC 2.0 IPC with Non-Blocking `asyncio.create_task` Dispatch**
   - **Decision:** Communicate between Rust and Python over line-delimited stdin/stdout JSON-RPC (`IpcDaemon`), redirecting `Rich` logging strictly to `stderr` and subprocess `stdin` to `DEVNULL`.
   - **Rationale:** Avoids local HTTP port collisions or firewall prompts for desktop usage while ensuring long builds never starve `system.ping` heartbeats or `build.cancel`, and preventing Windows child handle inheritance deadlocks on `fd 0`.

3. **ADR-003 — Thin Optional FastAPI Adapter (`backend/app/api/`)**
   - **Decision:** Keep FastAPI purely as an optional adapter wrapping shared `backend/app/services/*`, with zero business logic inside routers.
   - **Rationale:** Satisfies CI/automation HTTP integrations while preserving desktop independence from local web servers.

4. **ADR-004 — Parent Workspace Checklist & Non-Destructive Static GitHub Autoscan**
   - **Decision:** When scanning a parent workspace folder, inspect 1-level subdirectories for `.git` repositories and present a selectable checklist in the UI rather than blindly importing all folders; restrict GitHub scanning to static config, `git ls-remote`, workflow/lockfile inspection, and public REST API queries without executing host scripts.
   - **Rationale:** Prevents accidental ingestion of unrelated repositories and preserves static non-destructive host safety guarantees.

5. **ADR-005 — Strict 3-Tier Remediation Policy & Disposable Workspace Copies**
   - **Decision:** Separate Level 1 (auto-repairable inside disposable copy workspace), Level 2 (approval token required before disposable copy test), and Level 3 (strictly diagnosis-only).
   - **Rationale:** Eliminates unreviewed mutations to developer working trees and pairs every repair with independent dual-pass SHA-256 verification.
