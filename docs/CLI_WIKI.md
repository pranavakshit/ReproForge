# ReproForge CLI & Automation Wiki

**Complete Reference Guide for the `reproforge` Command-Line Interface, Launch Scripts, Configuration, and Headless API**

---

## Table of Contents
1. [Installation & Entrypoints](#1-installation--entrypoints)
2. [Global Options](#2-global-options)
3. [Command Reference](#3-command-reference)
   - [`reproforge info`](#reproforge-info)
   - [`reproforge fingerprint`](#reproforge-fingerprint)
   - [`reproforge detect`](#reproforge-detect)
   - [`reproforge repo add`](#reproforge-repo-add)
   - [`reproforge repo scan`](#reproforge-repo-scan)
   - [`reproforge build`](#reproforge-build)
   - [`reproforge compare`](#reproforge-compare)
   - [`reproforge daemon`](#reproforge-daemon)
4. [Root Launchers (Desktop GUI & API Mode)](#4-root-launchers-desktop-gui--api-mode)
5. [Optional Headless FastAPI Endpoints](#5-optional-headless-fastapi-endpoints)
6. [Environment Variables & Data Paths (`Settings`)](#6-environment-variables--data-paths-settings)
7. [End-to-End CLI Recipes](#7-end-to-end-cli-recipes)
8. [Test & Benchmark Commands](#8-test--benchmark-commands)
9. [Current CLI vs. Desktop UI Feature Differences](#9-current-cli-vs-desktop-ui-feature-differences)

---

## 1. Installation & Entrypoints

ReproForge installs the `reproforge` console script via `pyproject.toml` (`reproforge = "backend.app.cli.main:cli"`).

### Invoking the CLI
From the repository root with `.venv` active (or directly via `.venv`):

```powershell
# Windows PowerShell (without activating .venv)
.\.venv\Scripts\reproforge.exe --help

# Or via Python module directly
.\.venv\Scripts\python.exe -m backend.app.cli --help
```

```bash
# Linux / macOS
./.venv/bin/reproforge --help
# or:
./.venv/bin/python -m backend.app.cli --help
```

### Installing / Refreshing Editable Package
If `reproforge.exe` (or `reproforge` on POSIX) is missing inside `.venv`:

```powershell
.\.venv\Scripts\pip.exe install -e .
```

---

## 2. Global Options

```text
Usage: reproforge [OPTIONS] COMMAND [ARGS]...
```

| Flag | Description |
| :--- | :--- |
| `--version` | Prints `ReproForge, version 0.1.0` and exits |
| `--help` | Prints top-level command help and exits |

Available top-level commands:
`info`, `fingerprint`, `detect`, `repo` (`add`, `scan`), `build`, `compare`, `daemon`.

---

## 3. Command Reference

### `reproforge info`
Displays host OS/architecture, CPU cores, total RAM, masked hostname ID, workspace/artifact directories, and all detected compilers/tools (`python`, `git`, `docker`, `gcc`, `clang`, `node`, `cargo`, etc.) with version strings.

```powershell
.\.venv\Scripts\reproforge.exe info
```

---

### `reproforge fingerprint`
Captures a full structured environment snapshot (`SystemInfo`, kernel release, `LANG`/`TZ`/`umask`, detected toolchains, container runtime availability, and secret-filtered environment variables) and calculates its canonical SHA-256 `fingerprint_hash`.

Any environment key matching secret patterns (`key`, `secret`, `token`, `pass`, `auth`, `credential`, `cert`, `jwt`, `signature`, `private`) is automatically filtered/redacted.

**Options:**

| Option | Default | Description |
| :--- | :--- | :--- |
| `--json-out` | `False` | Print raw structured JSON instead of summary tables |

**Examples:**

```powershell
# Human-readable Rich tables
.\.venv\Scripts\reproforge.exe fingerprint

# Machine-readable JSON output
.\.venv\Scripts\reproforge.exe fingerprint --json-out
.\.venv\Scripts\reproforge.exe fingerprint --json-out > host_fingerprint.json
```

---

### `reproforge detect`
Statically scans a local directory to detect supported build systems (`docker`, `python`, `node`, `rust`, `go`, `make`, `github_actions`), primary config files, lockfiles, declared dependencies count, and suggested build commands. Does **not** run any repository code.

**Arguments:**

| Argument | Type | Description |
| :--- | :--- | :--- |
| `REPO_PATH` | Required directory | Path to an existing local project directory |

**Example:**

```powershell
.\.venv\Scripts\reproforge.exe detect D:\Repositories\ReproForge
.\.venv\Scripts\reproforge.exe detect D:\Repositories\LocAi
```

---

### `reproforge repo add`
Registers a local Git repository (or GitHub URL) into `data/reproforge.db`, runs static reproducibility readiness checks (lockfiles, mutable Docker tags, unpinned GitHub Actions, `SOURCE_DATE_EPOCH`), and records upstream GitHub remote info.

**Behavior when given a parent workspace folder (non-Git directory):**
If `TARGET` is an existing directory without `.git`, `repo add` scans 1-level child directories for Git repos, prints a numbered table, and prompts interactively (`comma-separated indices`, `'all'`, or `'none'`) to batch-register and autoscan selected repos.

**Arguments & Options:**

| Argument / Option | Default | Description |
| :--- | :--- | :--- |
| `TARGET` | Required (`str`) | Local repository path, parent workspace folder, or remote URL |
| `--name TEXT` | Inferred folder/repo name | Custom display name in ReproForge |
| `--project TEXT` | `default` | Logical project group name |
| `--online / --offline` | `--online` | Fetch public read-only GitHub API metadata vs. skip network lookup |

**Examples:**

```powershell
# Register a single local repository with GitHub API metadata lookup
.\.venv\Scripts\reproforge.exe repo add D:\Repositories\ReproForge

# Register offline (local Git remotes & static files only)
.\.venv\Scripts\reproforge.exe repo add D:\Repositories\LocAi --offline --name LocAi

# Scan a parent folder and interactively select child repos to track
.\.venv\Scripts\reproforge.exe repo add D:\Repositories
```

---

### `reproforge repo scan`
Discovers all 1-level child Git repositories inside `FOLDER_PATH`, showing each repository's default branch, linked GitHub `owner/repo` slug, and whether it is already tracked in SQLite (`Yes` / `No`). Read-only inspection only.

**Arguments:**

| Argument | Type | Description |
| :--- | :--- | :--- |
| `FOLDER_PATH` | Required directory | Parent folder containing Git repositories |

**Example:**

```powershell
.\.venv\Scripts\reproforge.exe repo scan D:\Repositories
```

---

### `reproforge build`
Exports a clean source tree for the specified Git revision (`git archive` into `build_workspaces/<build_id>/source`), records a host environment fingerprint in SQLite, executes the build in the selected isolation backend, updates build status (`completed`, `failed`, `timed_out`, `cancelled`), and harvests artifacts (`.whl`, `.tar.gz`, `.zip`, `.tar*`, `docker save` tarballs, or clean workspace tar bundles) into `collected_artifacts/<build_id>/`.

> **Isolation Note:** Local builds automatically provision an isolated `.venv` inside `build_workspaces/<build_id>/source/.venv` (using `sys.base_prefix` and stripping ReproForge's own `VIRTUAL_ENV`, `PYTHONPATH`, and `.venv/Scripts` from `PATH`). Docker builds run `docker build --no-cache --network=none` when a `Dockerfile` exists, or run inside a cached `python:*` image / fallback sandbox.

**Options:**

| Option | Type / Choices | Default | Description |
| :--- | :--- | :--- | :--- |
| `--repo DIRECTORY` | Existing directory | **Required** | Path to the target Git repository |
| `--commit TEXT` | Commit SHA, branch, or tag | `HEAD` | Git revision to export and build |
| `--backend` | `local` \| `docker` | `local` | Build execution backend |
| `--command TEXT` | Command string | Auto-detected | Custom shell build command overriding detector suggestion |
| `--timeout INTEGER` | Seconds | `1200` | Maximum allowed execution duration before timeout (`exit_code = 124`) |

**Examples:**

```powershell
# Default local sandbox build at HEAD
.\.venv\Scripts\reproforge.exe build --repo D:\Repositories\ReproForge

# Build a specific commit in Docker isolation with a 5-minute timeout
.\.venv\Scripts\reproforge.exe build --repo D:\Repositories\MyApp --commit main --backend docker --timeout 300

# Override build command explicitly
.\.venv\Scripts\reproforge.exe build --repo D:\Repositories\MyApp --command "python -m compileall -q ."
```

---

### `reproforge compare`
Compares any two files, archives (`.tar`, `.tar.gz`, `.zip`, `.whl`, Docker `.tar`), or build directories across both **Mode A (Strict Byte-for-Bit SHA-256)** and **Mode B (Normalized Structural Equivalence)**. Evaluates the 11 root-cause diagnostic rules (`DiagnosticEngine`), computes the multi-dimensional reproducibility vector + secondary CRI (`0.0–1.0`), and prints differences, root causes, and 3-tier remediation guidance.

**Arguments:**

| Argument | Type | Description |
| :--- | :--- | :--- |
| `ARTIFACT1` | Required file or directory | Baseline build artifact or folder |
| `ARTIFACT2` | Required file or directory | Second build artifact or folder |

**Examples:**

```powershell
# Compare two built wheels
.\.venv\Scripts\reproforge.exe compare .\collected_artifacts\build-a\pkg-0.1.0-py3-none-any.whl .\collected_artifacts\build-b\pkg-0.1.0-py3-none-any.whl

# Compare two directories or Docker image tarballs
.\.venv\Scripts\reproforge.exe compare .\collected_artifacts\bld1\docker_image_bld1.tar .\collected_artifacts\bld2\docker_image_bld2.tar
```

---

### `reproforge daemon`
Starts the newline-delimited JSON-RPC 2.0 daemon reading requests on `stdin` and emitting responses/notifications on `stdout`. Normally launched and supervised automatically by the Rust desktop binary (`ProcessSupervisor`), but can also be invoked manually for IPC debugging or custom clients.

**Options:**

| Option | Default | Description |
| :--- | :--- | :--- |
| `--no-log-file` | `False` | Write backend logs to `stderr` instead of log file (keeps `stdout` strictly JSON-RPC) |

**Supported JSON-RPC Methods:**
- `system.handshake`, `system.ping`, `system.shutdown`
- `overview.get_metrics`, `fingerprint.capture`
- `repository.list`, `repository.register`, `repository.scan_folder`, `repository.register_batch`, `repository.autoscan`, `repository.get_revisions`
- `build.list`, `build.get_status`, `build.get_details`, `build.trigger`, `build.verify_pair`, `build.cancel`
- `comparison.get_latest`, `comparison.list`, `comparison.get_details`, `comparison.compare_latest`
- `causality.get_graph`
- `perturbation.params`, `perturbation.run`, `perturbation.list`
- `passport.get`, `passport.export`
- `remediation.list_proposals`, `remediation.approve`, `remediation.verify_rebuild`

---

## 4. Root Launchers (Desktop GUI & API Mode)

| Platform / Script | Command | Behavior |
| :--- | :--- | :--- |
| Windows PowerShell | `.\start.ps1` | Launches desktop studio (Hardware FemtoVG renderer) |
| Windows PowerShell (low RAM) | `.\start.ps1 -Renderer software` | CPU software renderer (~41 MB idle RAM) |
| Windows PowerShell (rebuild Rust) | `.\start.ps1 -Build` | Compiles `cargo build --release` first, then launches GUI |
| Windows PowerShell (REST API) | `.\start.ps1 -Api -Port 8765` | Runs `alembic upgrade head` & launches FastAPI (`uvicorn`) |
| Windows PowerShell (help) | `.\start.ps1 -Help` | Displays launcher options |
| Windows CMD / Double-Click | `start.bat` | Delegates arguments to `start.ps1` |
| Alternate Desktop Launcher | `reproforge.bat` | Delegates directly to `scripts\launch_desktop.ps1` |
| Linux / macOS | `./start.sh [hardware\|software]` | Launches desktop studio via `scripts/launch_desktop.sh` |
| Linux / macOS (REST API) | `./start.sh --api [PORT]` | Runs DB migrations & starts FastAPI server on `127.0.0.1` |

---

## 5. Optional Headless FastAPI Endpoints

Start with `.\start.ps1 -Api` (or `.\.venv\Scripts\uvicorn.exe backend.app.api.app:app --host 127.0.0.1 --port 8765`):

| Method & Route | Description |
| :--- | :--- |
| `GET /health` | Service status & version |
| `GET /api/overview` | Reproducibility metrics, verdict breakdown, host telemetry, recent 8 builds |
| `GET /api/repositories` | Tracked repositories list |
| `POST /api/repositories/scan-folder` | Body: `{"folder_path": "..."}` — discovers child Git repos |
| `GET /api/builds?limit=50` | Lists builds (`limit` 1–500) |
| `GET /api/causality/latest` | Latest build comparison causality DAG (`nodes`, `edges`) |
| `GET /api/passports/{repository_id}` | Secret-redacted Reproducibility Passport v1.0 JSON |
| `POST /api/perturbation/{repository_id}` | Body: `{"commit_ref": "HEAD", "param_names": [...], "execute_real_builder": false}` |
| `GET /api/remediation?repository_id=...` | Lists generated Level 1 / 2 / 3 remediation proposals |
| `POST /api/remediation/{proposal_id}/approve` | Body: `{"approved": true, "repository_id": "..."}` — tests fix on disposable copies |
| `GET /api/reports/comparison/{repository_id}?format=json\|csv\|html` | Exports comparison/audit deliverable (`json`, `csv`, or `html`) |

Interactive Swagger/OpenAPI docs are available automatically at `http://127.0.0.1:8765/docs` while running.

---

## 6. Environment Variables & Data Paths (`Settings`)

Defined in `backend/app/core/config.py` (supports `.env` in repository root or shell environment variables):

| Setting Key | Default | Description |
| :--- | :--- | :--- |
| `DEBUG` | `False` | Enable debug mode |
| `DATA_DIR` | `<repo>/data` | Stores SQLite DB (`reproforge.db`), perturbation campaigns, remediation state |
| `WORKSPACES_DIR` | `<repo>/build_workspaces` | Isolated exported Git build workspaces (`<build_id>/source`) and build logs |
| `ARTIFACTS_DIR` | `<repo>/collected_artifacts` | Collected output `.whl`, `.tar`, `.zip`, and Docker image tarballs |
| `DATABASE_URL` | `sqlite+aiosqlite:///<DATA_DIR>/reproforge.db` | Async SQLAlchemy connection string (WAL mode) |
| `DEFAULT_BUILD_TIMEOUT_SECONDS` | `1200` | Default build command timeout (`20` minutes) |
| `MAX_CONCURRENT_BUILDS` | `2` | Maximum concurrent builds setting |

---

## 7. End-to-End CLI Recipes

### Recipe A — Quick Two-Build Check on Any Local Repo
```powershell
# 1. Inspect detected build config statically
.\.venv\Scripts\reproforge.exe detect D:\Repositories\MyRepo

# 2. Onboard repo and scan remotes/warnings
.\.venv\Scripts\reproforge.exe repo add D:\Repositories\MyRepo

# 3. Run two clean builds back-to-back (watch output paths in collected_artifacts/)
.\.venv\Scripts\reproforge.exe build --repo D:\Repositories\MyRepo --commit HEAD
.\.venv\Scripts\reproforge.exe build --repo D:\Repositories\MyRepo --commit HEAD

# 4. Compare both resulting artifacts directly
.\.venv\Scripts\reproforge.exe compare .\collected_artifacts\<build_id_1>\<artifact> .\collected_artifacts\<build_id_2>\<artifact>
```

### Recipe B — Cross-Check Host Environment Before Reporting Bugs
```powershell
.\.venv\Scripts\reproforge.exe info
.\.venv\Scripts\reproforge.exe fingerprint --json-out
```

### Recipe C — Run Alembic Migrations Fresh
```powershell
.\.venv\Scripts\alembic.exe upgrade head
```

---

## 8. Test & Benchmark Commands

| Check | Command | Scope |
| :--- | :--- | :--- |
| Full Python test suite | `.\.venv\Scripts\pytest.exe -q` | Unit, integration, service, comparison, perturbation, remediation, IPC & API tests |
| Rust desktop tests | `cargo test --manifest-path desktop/Cargo.toml` | Python discovery, live IPC ping/handshake, daemon crash auto-restart |
| Section 10 benchmark suite | `.\.venv\Scripts\python.exe experiments\benchmarks\repro_benchmark.py` | 10-scenario verdict/category/latency benchmark |
| Desktop memory & startup profiler | `.\.venv\Scripts\python.exe desktop\benchmarks\run_desktop_bench.py` | First-frame latency, idle RAM, and 200–50k row scaling benchmarks |

---

## 9. Current CLI vs. Desktop UI Feature Differences

- **CLI currently exposes directly:** arbitrary file/folder comparison (`reproforge compare <a1> <a2>`), explicit `--command` / `--timeout` overrides on builds, standalone `fingerprint --json-out`, and detailed lockfile/dependency counts in `detect`.
- **Desktop UI currently exposes directly (beyond CLI):** visual 9-screen workflow, **Build Drift Causality Graph** viewer & `.dot` export, **Perturbation Lab** single-variable campaigns, **Passport v1.0** viewer & JSON/Markdown export, **3-Tier Remediation** approval cockpit, 1-click **Verify Reproducibility (2x + Compare)**, and live log streaming.
- **Headless REST API (`-Api`)** bridges campaign execution, remediation approval, causality graphs, passports, and HTML/CSV/JSON report exports for CI scripts without launching the GUI.
