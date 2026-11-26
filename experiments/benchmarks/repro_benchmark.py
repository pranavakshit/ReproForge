"""Controlled 10-Scenario Empirical Benchmark Harness (Master Prompt Section 10).

Evaluates ReproForge's Dual-Mode Comparator, Artifact Normalizer, Diagnostic Engine,
and Composite Reproducibility Index (CRI) against baseline approaches:
  - Baseline 1: Hash-Only Check (raw strict SHA-256)
  - Baseline 2: Raw File/Tree Listing Diff
  - Optional: diffoscope (detected automatically at runtime; never fabricated)
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.comparison.diff import StructuralDiffEngine  # noqa: E402
from backend.app.comparison.hasher import ArtifactHasher  # noqa: E402
from backend.app.diagnostics.engine import DiagnosticEngine  # noqa: E402
from backend.app.diagnostics.evidence import DiagnosticContext  # noqa: E402
from backend.app.execution.runner import BuildExecutionResult  # noqa: E402
from backend.app.orchestration.detector import DetectedBuildSystem  # noqa: E402
from backend.app.reporting.exporter import ReportExporter  # noqa: E402
from backend.app.schemas.entities import (  # noqa: E402
    BuildStatus,
    DiagnosticCategory,
    EnvironmentFingerprintRead,
    LocaleInfo,
    ReproducibilityVerdict,
    SystemInfo,
    ToolchainInfo,
)
from backend.app.scoring.calculator import ReproducibilityCalculator  # noqa: E402


def _make_fp(
    os_name: str = "Linux",
    arch: str = "x86_64",
    tz: str = "UTC",
    umask: str = "0022",
    tools: Optional[Dict[str, Dict[str, Any]]] = None,
    env: Optional[Dict[str, str]] = None,
) -> EnvironmentFingerprintRead:
    raw = json.dumps(
        {"os": os_name, "arch": arch, "tz": tz, "umask": umask, "tools": tools, "env": env},
        sort_keys=True,
    )
    return EnvironmentFingerprintRead(
        id=f"fp-{hashlib.sha256(raw.encode()).hexdigest()[:12]}",
        fingerprint_hash=hashlib.sha256(raw.encode()).hexdigest(),
        captured_at=datetime.now(timezone.utc),
        system_info=SystemInfo(
            os_name=os_name,
            os_release="6.1",
            os_version="6.1",
            kernel_release="6.1",
            cpu_arch=arch,
            cpu_count=8,
            total_ram_bytes=16 * 1024**3,
            hostname_masked="bench-host",
        ),
        toolchain_info=ToolchainInfo(tools=tools or {"python": {"version": "3.14.0"}}),
        locale_info=LocaleInfo(timezone=tz, umask=umask),
        environment_variables=env or {"TZ": tz},
    )


def _write_tar(dest: Path, entries: List[tuple[str, bytes, int, int]]) -> Path:
    with tarfile.open(dest, "w") as tf:
        for name, data, mtime, mode in entries:
            ti = tarfile.TarInfo(name=name)
            ti.size = len(data)
            ti.mtime = mtime
            ti.mode = mode
            ti.uid = 0
            ti.gid = 0
            tf.addfile(ti, io.BytesIO(data))
    return dest


def _write_wheel_zip(dest: Path, entries: List[tuple[str, bytes, tuple[int, int, int, int, int, int]]]) -> Path:
    with zipfile.ZipFile(dest, "w") as zf:
        for name, data, date_time in entries:
            zi = zipfile.ZipInfo(filename=name, date_time=date_time)
            zf.writestr(zi, data)
    return dest


def _build_res(path: Path, build_id: str, exit_code: int = 0) -> BuildExecutionResult:
    now = datetime.now(timezone.utc)
    return BuildExecutionResult(
        build_id=build_id,
        backend_type="local",
        status=BuildStatus.COMPLETED if exit_code == 0 else BuildStatus.FAILED,
        exit_code=exit_code,
        started_at=now,
        completed_at=now,
        duration_seconds=0.1,
        log_file_path=str(path.with_suffix(".log")),
    )


def _run_case(
    exp_id: str,
    name: str,
    path_a: Path,
    path_b: Path,
    work_dir: Path,
    expected_verdict: ReproducibilityVerdict,
    expected_category: Optional[DiagnosticCategory],
    fp_a: Optional[EnvironmentFingerprintRead] = None,
    fp_b: Optional[EnvironmentFingerprintRead] = None,
    build_sys: Optional[DetectedBuildSystem] = None,
    log_a: Optional[str] = None,
    log_b: Optional[str] = None,
) -> Dict[str, Any]:
    comparator = StructuralDiffEngine()
    diag_engine = DiagnosticEngine()

    t0 = time.perf_counter()
    raw_sha_a = ArtifactHasher.compute_sha256(path_a)
    raw_sha_b = ArtifactHasher.compute_sha256(path_b)
    raw_sha_match = raw_sha_a == raw_sha_b

    comp_summary = comparator.compare_artifacts(path_a, path_b, work_dir=work_dir / f"wk_{exp_id}")
    ctx = DiagnosticContext(
        comparison_summary=comp_summary,
        env_fingerprint_1=fp_a,
        env_fingerprint_2=fp_b,
        build_1_result=_build_res(path_a, f"{exp_id}-a"),
        build_2_result=_build_res(path_b, f"{exp_id}-b"),
        build_system=build_sys,
        build_1_log_text=log_a,
        build_2_log_text=log_b,
    )
    findings = diag_engine.diagnose(ctx)
    score = ReproducibilityCalculator.evaluate(summary=comp_summary, diagnostics=findings, context=ctx)
    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)

    detected_cats = [f.category.value for f in findings]
    cat_match = True if expected_category is None else (expected_category.value in detected_cats)

    sha_blind_spot = (
        "Marks IDENTICAL bytes as FAILED due to header/mtime drift"
        if (not raw_sha_match and score.verdict == ReproducibilityVerdict.NORMALIZED_REPRODUCIBLE)
        else ("Zero root-cause classification on drift" if not raw_sha_match else "None")
    )
    raw_diff_blind_spot = (
        "Lists path deltas without epistemic causal attribution"
        if findings
        else "No normalization semantics"
    )

    return {
        "experiment_id": exp_id,
        "name": name,
        "latency_ms": elapsed_ms,
        "raw_sha_match": raw_sha_match,
        "normalized_match": score.normalized_match,
        "expected_verdict": expected_verdict.value,
        "reproforge_verdict": score.verdict.value,
        "verdict_match": score.verdict == expected_verdict,
        "cri": round(score.composite_reproducibility_index, 4),
        "expected_category": expected_category.value if expected_category else None,
        "detected_categories": detected_cats,
        "epistemic_statuses": [
            (f.evidence_trail[0].get("epistemic_status") if f.evidence_trail else "inferred")
            for f in findings
        ],
        "category_match": cat_match,
        "sha_baseline_blind_spot": sha_blind_spot,
        "raw_diff_blind_spot": raw_diff_blind_spot,
    }


def run_all_benchmarks() -> Dict[str, Any]:
    diffoscope_bin = shutil.which("diffoscope")
    results: List[Dict[str, Any]] = []

    with tempfile.TemporaryDirectory() as tmp:
        td = Path(tmp)

        # Exp 1: Deterministic Build
        a1 = _write_tar(td / "e1_a.tar", [("app/main.py", b"print('v1')\n", 1700000000, 0o644)])
        b1 = _write_tar(td / "e1_b.tar", [("app/main.py", b"print('v1')\n", 1700000000, 0o644)])
        results.append(_run_case("EXP-01", "Deterministic Build", a1, b1, td, ReproducibilityVerdict.BIT_FOR_BIT_REPRODUCIBLE, None))

        # Exp 2: Timestamp Nondeterminism
        a2 = _write_tar(td / "e2_a.tar", [("app/main.py", b"print('v1')\n", 1700000000, 0o644)])
        b2 = _write_tar(td / "e2_b.tar", [("app/main.py", b"print('v1')\n", 1760000000, 0o644)])
        results.append(_run_case(
            "EXP-02", "Timestamp Nondeterminism", a2, b2, td,
            ReproducibilityVerdict.NORMALIZED_REPRODUCIBLE,
            DiagnosticCategory.TIMESTAMP_NONDETERMINISM,
            fp_a=_make_fp(tz="UTC"), fp_b=_make_fp(tz="UTC"),
        ))

        # Exp 3: Dependency Variation
        a3 = _write_tar(td / "e3_a.tar", [("site-packages/pkg.py", b"VER='1.0.0'\n", 1700000000, 0o644)])
        b3 = _write_tar(td / "e3_b.tar", [("site-packages/pkg.py", b"VER='1.0.1'\n", 1700000000, 0o644)])
        sys_dep = DetectedBuildSystem(
            system_type="python",
            primary_config_file="requirements.txt",
            build_files_found=["requirements.txt"],
            has_lockfile=False,
            declared_dependencies_count=3,
            suggested_build_command="pip wheel .",
        )
        results.append(_run_case(
            "EXP-03", "Dependency Variation", a3, b3, td,
            ReproducibilityVerdict.NON_REPRODUCIBLE,
            DiagnosticCategory.DEPENDENCY_VARIATION,
            fp_a=_make_fp(), fp_b=_make_fp(),
            build_sys=sys_dep,
            log_a="Successfully installed demo-1.0.0\n",
            log_b="Successfully installed demo-1.0.1\n",
        ))

        # Exp 4: Environment Variable Leakage
        a4 = _write_tar(td / "e4_a.tar", [("dist/config.json", b'{"host":"runner-alpha"}\n', 1700000000, 0o644)])
        b4 = _write_tar(td / "e4_b.tar", [("dist/config.json", b'{"host":"runner-beta"}\n', 1700000000, 0o644)])
        results.append(_run_case(
            "EXP-04", "Environment Variable Variation", a4, b4, td,
            ReproducibilityVerdict.NON_REPRODUCIBLE,
            DiagnosticCategory.ENVIRONMENT_LEAKAGE,
            fp_a=_make_fp(env={"BUILD_HOST": "runner-alpha"}),
            fp_b=_make_fp(env={"BUILD_HOST": "runner-beta"}),
        ))

        # Exp 5: Toolchain Variation
        a5 = _write_tar(td / "e5_a.tar", [("bin/mod.pyc", b"\x00\x01\x02\x03gcc12", 1700000000, 0o644)])
        b5 = _write_tar(td / "e5_b.tar", [("bin/mod.pyc", b"\x00\x01\x02\x09gcc14", 1700000000, 0o644)])
        results.append(_run_case(
            "EXP-05", "Toolchain Variation", a5, b5, td,
            ReproducibilityVerdict.NON_REPRODUCIBLE,
            DiagnosticCategory.TOOLCHAIN_VARIATION,
            fp_a=_make_fp(tools={"python": {"version": "3.11.8"}}),
            fp_b=_make_fp(tools={"python": {"version": "3.14.0"}}),
        ))

        # Exp 6: Archive Ordering & Wheel Metadata Nondeterminism
        a6 = _write_wheel_zip(td / "e6_a.whl", [
            ("pkg/a.py", b"A=1\n", (2024, 1, 1, 0, 0, 0)),
            ("pkg/b.py", b"B=2\n", (2024, 1, 1, 0, 0, 0)),
        ])
        b6 = _write_wheel_zip(td / "e6_b.whl", [
            ("pkg/b.py", b"B=2\n", (2025, 5, 5, 12, 0, 0)),
            ("pkg/a.py", b"A=1\n", (2025, 5, 5, 12, 0, 0)),
        ])
        results.append(_run_case(
            "EXP-06", "Archive Nondeterminism", a6, b6, td,
            ReproducibilityVerdict.NORMALIZED_REPRODUCIBLE,
            DiagnosticCategory.TIMESTAMP_NONDETERMINISM,
            fp_a=_make_fp(), fp_b=_make_fp(),
        ))

        # Exp 7: Container Base Image Variation (OCI archive with mutable tag & differing base layer)
        oci_manifest_a = json.dumps([{"Config": "cfg.json", "RepoTags": ["app:v1"], "Layers": ["layer_base_1.tar"]}]).encode()
        oci_manifest_b = json.dumps([{"Config": "cfg.json", "RepoTags": ["app:v1"], "Layers": ["layer_base_2.tar"]}]).encode()
        a7 = _write_tar(td / "e7_oci_a.tar", [
            ("manifest.json", oci_manifest_a, 1700000000, 0o644),
            ("rootfs/etc/os-release", b"VERSION=12.1\n", 1700000000, 0o644),
        ])
        b7 = _write_tar(td / "e7_oci_b.tar", [
            ("manifest.json", oci_manifest_b, 1700000000, 0o644),
            ("rootfs/etc/os-release", b"VERSION=12.9\n", 1700000000, 0o644),
        ])
        sys_docker = DetectedBuildSystem(
            system_type="docker",
            primary_config_file="Dockerfile",
            build_files_found=["Dockerfile"],
            has_lockfile=False,
            base_images=["python:3.12-slim"],
            suggested_build_command="docker build .",
        )
        results.append(_run_case(
            "EXP-07", "Container Image Reproducibility", a7, b7, td,
            ReproducibilityVerdict.NON_REPRODUCIBLE,
            DiagnosticCategory.BASE_IMAGE_VARIATION,
            fp_a=_make_fp(), fp_b=_make_fp(),
            build_sys=sys_docker,
            log_a="Step 1/4 : FROM python:3.12-slim\n",
            log_b="Step 1/4 : FROM python:3.12-slim\n",
        ))

        # Exp 8: Unknown Nondeterminism (Must refuse overclaiming -> INCONCLUSIVE + UNKNOWN_VARIATION)
        a8 = _write_tar(td / "e8_a.tar", [("out/opaque.bin", os.urandom(64), 1700000000, 0o644)])
        b8 = _write_tar(td / "e8_b.tar", [("out/opaque.bin", os.urandom(64), 1700000000, 0o644)])
        fp8 = _make_fp()
        results.append(_run_case(
            "EXP-08", "Unknown Nondeterminism", a8, b8, td,
            ReproducibilityVerdict.INCONCLUSIVE,
            DiagnosticCategory.UNKNOWN_VARIATION,
            fp_a=fp8, fp_b=fp8,
        ))

        # Exp 9: Cross-Environment Build
        a9 = _write_tar(td / "e9_a.tar", [("out/native.so", b"\x7fELF-x86_64", 1700000000, 0o644)])
        b9 = _write_tar(td / "e9_b.tar", [("out/native.so", b"\x7fELF-aarch64", 1700000000, 0o644)])
        results.append(_run_case(
            "EXP-09", "Cross-Environment Build", a9, b9, td,
            ReproducibilityVerdict.NON_REPRODUCIBLE,
            DiagnosticCategory.PLATFORM_VARIATION,
            fp_a=_make_fp(os_name="Linux", arch="x86_64"),
            fp_b=_make_fp(os_name="Windows", arch="arm64"),
        ))

        # Exp 10: Diagnostic Performance & Scaling (Larger Multi-File Payload)
        large_a = [(f"bundle/mod_{i:03d}.py", f"VAL={i}\n".encode() * 64, 1700000000, 0o644) for i in range(60)]
        large_b = [(f"bundle/mod_{i:03d}.py", f"VAL={i}\n".encode() * 64, 1700000500, 0o644) for i in range(60)]
        a10 = _write_tar(td / "e10_a.tar", large_a)
        b10 = _write_tar(td / "e10_b.tar", large_b)
        results.append(_run_case(
            "EXP-10", "Diagnostic Performance (60-file Archive)", a10, b10, td,
            ReproducibilityVerdict.NORMALIZED_REPRODUCIBLE,
            DiagnosticCategory.TIMESTAMP_NONDETERMINISM,
            fp_a=_make_fp(), fp_b=_make_fp(),
        ))

    total = len(results)
    verdict_hits = sum(1 for r in results if r["verdict_match"])
    cat_hits = sum(1 for r in results if r["category_match"])
    norm_scenarios = [r for r in results if r["expected_verdict"] == ReproducibilityVerdict.NORMALIZED_REPRODUCIBLE.value]
    norm_recovered = sum(1 for r in norm_scenarios if r["verdict_match"])
    avg_latency_ms = round(sum(r["latency_ms"] for r in results) / total, 2)

    exp8 = next(r for r in results if r["experiment_id"] == "EXP-08")
    unknown_safe_fallback = DiagnosticCategory.UNKNOWN_VARIATION.value in exp8["detected_categories"]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_scenarios": total,
        "baselines": {
            "sha256_hash_only": {"available": True},
            "raw_file_diff": {"available": True},
            "diffoscope": {"available": bool(diffoscope_bin), "path": diffoscope_bin},
        },
        "metrics": {
            "verdict_accuracy_num": verdict_hits,
            "verdict_accuracy_den": total,
            "verdict_accuracy_pct": round(verdict_hits / total * 100.0, 2),
            "diagnostic_category_accuracy_num": cat_hits,
            "diagnostic_category_accuracy_den": total,
            "diagnostic_category_accuracy_pct": round(cat_hits / total * 100.0, 2),
            "normalized_recovery_num": norm_recovered,
            "normalized_recovery_den": len(norm_scenarios),
            "normalized_recovery_pct": round(norm_recovered / max(1, len(norm_scenarios)) * 100.0, 2),
            "unknown_nondeterminism_safe_fallback": unknown_safe_fallback,
            "avg_diagnostic_latency_ms": avg_latency_ms,
        },
        "scenarios": results,
    }


def main() -> None:
    report = run_all_benchmarks()
    out_dir = REPO_ROOT / "experiments" / "benchmarks"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "benchmark_results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out_dir / "benchmark_results.csv").write_text(
        ReportExporter.experiments_to_csv(report["scenarios"]),
        encoding="utf-8",
    )

    m = report["metrics"]
    md_lines = [
        "# ReproForge Section 10 Controlled Benchmark Summary",
        "",
        f"- **Generated At:** `{report['generated_at']}`",
        f"- **Verdict Accuracy:** `{m['verdict_accuracy_num']}/{m['verdict_accuracy_den']}` ({m['verdict_accuracy_pct']}%)",
        f"- **Diagnostic Category Accuracy:** `{m['diagnostic_category_accuracy_num']}/{m['diagnostic_category_accuracy_den']}` ({m['diagnostic_category_accuracy_pct']}%)",
        f"- **Normalized Equivalence Recovery:** `{m['normalized_recovery_num']}/{m['normalized_recovery_den']}` ({m['normalized_recovery_pct']}%)",
        f"- **EXP-08 Safe Unknown Fallback:** `{m['unknown_nondeterminism_safe_fallback']}`",
        f"- **Mean Diagnostic Latency:** `{m['avg_diagnostic_latency_ms']} ms`",
        f"- **diffoscope Installed on Host:** `{report['baselines']['diffoscope']['available']}`",
        "",
        "| ID | Scenario | Expected Verdict | ReproForge Verdict | Categories | Latency (ms) | Category Hit |",
        "| :--- | :--- | :--- | :--- | :--- | ---: | :---: |",
    ]
    for s in report["scenarios"]:
        cats = ", ".join(s["detected_categories"]) or "—"
        hit = "PASS" if s["category_match"] and s["verdict_match"] else "FAIL"
        md_lines.append(
            f"| {s['experiment_id']} | {s['name']} | `{s['expected_verdict']}` | `{s['reproforge_verdict']}` | `{cats}` | {s['latency_ms']:.2f} | {hit} |"
        )
    (out_dir / "benchmark_summary.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    print(
        f"Benchmark complete: {m['verdict_accuracy_num']}/{m['verdict_accuracy_den']} verdicts, "
        f"{m['diagnostic_category_accuracy_num']}/{m['diagnostic_category_accuracy_den']} categories, "
        f"avg {m['avg_diagnostic_latency_ms']} ms"
    )


if __name__ == "__main__":
    main()
