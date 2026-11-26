# ReproForge Empirical Evaluation & Experiments Report

All measurements reported below were executed and logged directly in [benchmark_results.json](../experiments/benchmarks/benchmark_results.json), [benchmark_summary.md](../experiments/benchmarks/benchmark_summary.md), and [desktop_benchmark_results.json](../desktop/benchmarks/desktop_benchmark_results.json).

## 1. Controlled Reproducibility Benchmark Suite (`EXP-01` – `EXP-10`)
- **Execution Command:** `.\.venv\Scripts\python.exe experiments\benchmarks\repro_benchmark.py`
- **Timestamp:** `2026-11-26T07:02:21Z`
- **Host Tool Availability:** SHA-256 hash-only baseline (available), raw diff baseline (available), `diffoscope` (`False` / not installed on Windows test host — honestly reported as unavailable without synthetic extrapolation).

### Summary Metrics (with Explicit Denominators)
| Metric | Measured Score | Ratio / Numerator-Denominator |
| :--- | :---: | :---: |
| Verdict Accuracy | **100.0%** | `10 / 10` |
| Diagnostic Category Accuracy | **100.0%** | `10 / 10` |
| Normalized Equivalence Recovery | **100.0%** | `3 / 3` (`EXP-02`, `EXP-06`, `EXP-10`) |
| Safe Unknown Fallback (`EXP-08`) | **True** | `unknown_variation` -> `inconclusive` |
| Mean End-to-End Diagnostic Latency | **18.47 ms** | Across all 10 scenarios |

### Scenario-by-Scenario Measurements
| ID | Scenario | Expected Verdict | ReproForge Verdict | Detected Categories | Latency (ms) | Status |
| :--- | :--- | :--- | :--- | :--- | ---: | :---: |
| `EXP-01` | Deterministic Build | `bit_for_bit_reproducible` | `bit_for_bit_reproducible` | — | 17.77 | PASS |
| `EXP-02` | Timestamp Nondeterminism | `normalized_reproducible` | `normalized_reproducible` | `timestamp_nondeterminism` | 9.81 | PASS |
| `EXP-03` | Dependency Variation | `non_reproducible` | `non_reproducible` | `dependency_variation` | 10.40 | PASS |
| `EXP-04` | Environment Variable Variation | `non_reproducible` | `non_reproducible` | `environment_leakage` | 12.44 | PASS |
| `EXP-05` | Toolchain Variation | `non_reproducible` | `non_reproducible` | `toolchain_variation` | 10.66 | PASS |
| `EXP-06` | Archive Nondeterminism | `normalized_reproducible` | `normalized_reproducible` | `timestamp_nondeterminism`, `filesystem_ordering` | 12.70 | PASS |
| `EXP-07` | Container Image Reproducibility | `non_reproducible` | `non_reproducible` | `base_image_variation` | 14.71 | PASS |
| `EXP-08` | Unknown Nondeterminism | `inconclusive` | `inconclusive` | `unknown_variation` | 9.44 | PASS |
| `EXP-09` | Cross-Environment Build | `non_reproducible` | `non_reproducible` | `platform_variation` | 8.93 | PASS |
| `EXP-10` | Diagnostic Performance (60-file Archive) | `normalized_reproducible` | `normalized_reproducible` | `timestamp_nondeterminism`, `filesystem_ordering` | 77.80 | PASS |

### Comparison Against Baselines
- **Raw SHA-256 Baseline:** Marked `EXP-02`, `EXP-06`, and `EXP-10` (`3/3` normalized-equivalent cases) as failures without distinguishing metadata headers/ordering from functional code changes, and provided zero root-cause attribution across `EXP-03..EXP-09`.
- **Raw File/Tree Diff Baseline:** Surfaced changed entry names on content diffs, but failed to separate container manifest layer variance (`EXP-07`) or tag causal provenance / epistemic status.

---

## 2. Final Desktop Application Resource Profiling
- **Execution Command:** `.\.venv\Scripts\python.exe desktop\benchmarks\run_desktop_bench.py`
- **Target Binary:** `desktop/target/release/reproforge_desktop.exe` (`13.17 MB` on disk, including Slint vector icons, all 9 views, native OS folder dialog `rfd`, and Rust IPC supervisor)

| Configuration | Record Count | Avg First-Frame (ms) | Idle RAM (MB) | Idle CPU (%) |
| :--- | ---: | ---: | ---: | ---: |
| Slint Hardware (`FemtoVG`) | 200 | 673.6 | 117.43 | 2.60% |
| Slint Software (`--renderer=software`) | 200 | **121.4** | **41.32** | **0.52%** |
| Slint Hardware Scaling | 10,000 | 700.0 | 122.28 | 1.03% |
| Slint Hardware Scaling | 50,000 | 842.6 | 148.04 | 3.12% |

> [!NOTE]
> Even at `50,000` virtualized build rows while running live background IPC sync, hardware-rendered RAM grew by only `30.61 MB` above the `200`-row baseline, and software fallback remained at `41.32 MB` total working set.

---

## 3. Perturbation & Verification Suite Coverage
- **Python Automated Suite (`pytest`):** `106 passed` (including unit, service, database repository isolation, FastAPI headless routes, report exporters, perturbation confounder checks, passport redaction, 3-tier remediation policy gates, and concurrent IPC streaming/cancellation tests).
- **Rust Desktop & Daemon Integration Suite (`cargo test`):** `3 passed` (interpreter resolution, live Python JSON-RPC handshake/ping, crash detection & auto-restart).
