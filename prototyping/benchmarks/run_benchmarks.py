"""Empirical release benchmark harness for Slint vs. Iced desktop prototypes.

Measures:
1. Release executable binary size on disk.
2. First-frame presentation startup latency (process spawn to FIRST_FRAME_PRESENTED signal, 5 iterations).
3. Idle Working Set RAM (RSS) over steady-state window.
4. Idle CPU utilization percentage over steady-state window.
5. Large tabular scaling at N=200, N=10,000, and N=50,000 records with equivalent visible-row rendering.
6. GPU initialization failure and fallback verification.
"""

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
import psutil


def measure_binary_size(exe_path: Path) -> float:
    """Return binary size in megabytes."""
    return round(os.path.getsize(exe_path) / (1024 * 1024), 2)


def measure_first_frame_presentation(
    exe_path: Path, env: Optional[Dict[str, str]] = None, iterations: int = 3
) -> Dict[str, float]:
    """Measure process launch to FIRST_FRAME_PRESENTED signal across multiple runs."""
    durations: List[float] = []

    for _ in range(iterations):
        t0 = time.perf_counter()
        proc = subprocess.Popen(
            [str(exe_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            env=env,
        )
        try:
            while True:
                if time.perf_counter() - t0 > 10.0:
                    break
                line = proc.stdout.readline()
                if not line:
                    break
                if "FIRST_FRAME_PRESENTED" in line:
                    durations.append((time.perf_counter() - t0) * 1000.0)
                    break
        finally:
            try:
                proc.kill()
                proc.wait(timeout=1.0)
            except Exception:
                pass
            time.sleep(0.1)

    if not durations:
        return {"avg_ms": 0.0, "min_ms": 0.0, "max_ms": 0.0}

    avg_startup = round(sum(durations) / len(durations), 1)
    min_startup = round(min(durations), 1)
    max_startup = round(max(durations), 1)
    return {"avg_ms": avg_startup, "min_ms": min_startup, "max_ms": max_startup}


def measure_idle_footprint(
    exe_path: Path, env: Optional[Dict[str, str]] = None, duration_seconds: int = 4
) -> Dict[str, Any]:
    """Measure steady-state idle RAM and CPU utilization over a timed duration."""
    proc = subprocess.Popen(
        [str(exe_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=env,
    )
    p = psutil.Process(proc.pid)

    # Let UI present initial frame and stabilize
    time.sleep(1.5)

    rss_samples: List[float] = []
    cpu_samples: List[float] = []

    try:
        start_time = time.time()
        try:
            p.cpu_percent(interval=None)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

        while time.time() - start_time < duration_seconds:
            try:
                mem_mb = p.memory_info().rss / (1024 * 1024)
                rss_samples.append(mem_mb)
                cpu = p.cpu_percent(interval=0.5)
                cpu_samples.append(cpu)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                break

    finally:
        try:
            proc.kill()
            proc.wait(timeout=1.0)
        except Exception:
            pass

    avg_ram = round(sum(rss_samples) / len(rss_samples), 2) if rss_samples else 0.0
    min_ram = round(min(rss_samples), 2) if rss_samples else 0.0
    max_ram = round(max(rss_samples), 2) if rss_samples else 0.0
    avg_cpu = round(sum(cpu_samples) / len(cpu_samples), 2) if cpu_samples else 0.0

    return {
        "avg_ram_mb": avg_ram,
        "min_ram_mb": min_ram,
        "max_ram_mb": max_ram,
        "avg_cpu_percent": avg_cpu,
        "samples_count": len(rss_samples),
    }

    avg_ram = round(sum(rss_samples) / len(rss_samples), 2) if rss_samples else 0.0
    min_ram = round(min(rss_samples), 2) if rss_samples else 0.0
    max_ram = round(max(rss_samples), 2) if rss_samples else 0.0
    avg_cpu = round(sum(cpu_samples) / len(cpu_samples), 2) if cpu_samples else 0.0

    return {
        "avg_ram_mb": avg_ram,
        "min_ram_mb": min_ram,
        "max_ram_mb": max_ram,
        "avg_cpu_percent": avg_cpu,
        "samples_count": len(rss_samples),
    }


def run_benchmark_suite(
    name: str,
    exe_path: Path,
    env_extra: Optional[Dict[str, str]] = None,
    n_records: int = 200,
    duration_seconds: int = 15,
) -> Dict[str, Any]:
    """Run full benchmark for a framework configuration at a specific dataset size."""
    env = os.environ.copy()
    env["REPROFORGE_BENCH_RECORDS"] = str(n_records)
    if env_extra:
        env.update(env_extra)

    size_mb = measure_binary_size(exe_path)
    startup = measure_first_frame_presentation(exe_path, env=env, iterations=5)
    idle = measure_idle_footprint(exe_path, env=env, duration_seconds=duration_seconds)

    print(
        f"[{name:<20} | N={n_records:>5}] Binary: {size_mb:>5.2f} MB | "
        f"First-Frame: {startup['avg_ms']:>6.1f} ms | Idle RAM: {idle['avg_ram_mb']:>6.2f} MB | CPU: {idle['avg_cpu_percent']:>4.1f}%"
    )

    return {
        "framework": name,
        "records_count": n_records,
        "binary_size_mb": size_mb,
        "first_frame_presentation_ms": startup,
        "idle_telemetry": idle,
    }


def verify_gpu_fallback(exe_path: Path) -> Dict[str, Any]:
    """Verify that when GPU adapter initialization fails, the app falls back to tiny-skia."""
    env = os.environ.copy()
    env["WGPU_BACKEND"] = "empty"
    env["WGPU_ADAPTER_NAME"] = "NONEXISTENT_DEVICE_SIMULATOR"

    p = subprocess.Popen(
        [str(exe_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    line1 = p.stdout.readline().strip()
    line2 = p.stdout.readline().strip()

    time.sleep(1.0)
    proc = psutil.Process(p.pid)
    ram_mb = round(proc.memory_info().rss / (1024 * 1024), 2)

    p.terminate()
    try:
        p.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        p.kill()

    fallback_ok = "Tiny-Skia" in line1 and "FIRST_FRAME_PRESENTED" in line2
    print(
        f"[GPU Fallback Verification] Fallback: {'PASS' if fallback_ok else 'FAIL'} | Active: {line1} | RAM: {ram_mb} MB"
    )
    return {
        "fallback_successful": fallback_ok,
        "active_backend_line": line1,
        "first_frame_signal": line2,
        "fallback_idle_ram_mb": ram_mb,
    }


if __name__ == "__main__":
    slint_exe = Path("prototyping/benchmarks/slint_proto/target/release/reproforge_slint_bench.exe")
    iced_combined_exe = Path("prototyping/benchmarks/iced_proto/target/release/reproforge_iced_bench.exe")
    iced_skia_only_exe = Path("prototyping/benchmarks/iced_skia_proto/target/release/reproforge_iced_skia_bench.exe")

    results: Dict[str, Any] = {
        "baseline_200": {},
        "scaling_10000": {},
        "scaling_50000": {},
        "gpu_fallback_verification": {},
    }

    print("\n=== ReproForge Milestone B Empirical Benchmark (First-Frame Presentation) ===")

    # 1. Baseline 200 records
    print("\n--- 1. Baseline (N=200 records) ---")
    results["baseline_200"]["slint"] = run_benchmark_suite(
        "Slint", slint_exe, n_records=200, duration_seconds=4
    )
    results["baseline_200"]["iced_wgpu"] = run_benchmark_suite(
        "Iced (WGPU)", iced_combined_exe, env_extra={"ICED_BACKEND": "wgpu"}, n_records=200, duration_seconds=4
    )
    results["baseline_200"]["iced_tiny_skia"] = run_benchmark_suite(
        "Iced (Tiny-Skia)", iced_combined_exe, env_extra={"ICED_BACKEND": "tiny-skia"}, n_records=200, duration_seconds=4
    )

    # 2. Scaling N=10,000 records
    print("\n--- 2. Tabular Scaling (N=10,000 records) ---")
    results["scaling_10000"]["slint"] = run_benchmark_suite(
        "Slint", slint_exe, n_records=10000, duration_seconds=4
    )
    results["scaling_10000"]["iced_wgpu"] = run_benchmark_suite(
        "Iced (WGPU)", iced_combined_exe, env_extra={"ICED_BACKEND": "wgpu"}, n_records=10000, duration_seconds=4
    )
    results["scaling_10000"]["iced_tiny_skia"] = run_benchmark_suite(
        "Iced (Tiny-Skia)", iced_combined_exe, env_extra={"ICED_BACKEND": "tiny-skia"}, n_records=10000, duration_seconds=4
    )

    # 3. Scaling N=50,000 records
    print("\n--- 3. Tabular Scaling (N=50,000 records) ---")
    results["scaling_50000"]["slint"] = run_benchmark_suite(
        "Slint", slint_exe, n_records=50000, duration_seconds=4
    )
    results["scaling_50000"]["iced_wgpu"] = run_benchmark_suite(
        "Iced (WGPU)", iced_combined_exe, env_extra={"ICED_BACKEND": "wgpu"}, n_records=50000, duration_seconds=4
    )
    results["scaling_50000"]["iced_tiny_skia"] = run_benchmark_suite(
        "Iced (Tiny-Skia)", iced_combined_exe, env_extra={"ICED_BACKEND": "tiny-skia"}, n_records=50000, duration_seconds=4
    )

    # 4. GPU Failure Fallback Verification
    print("\n--- 4. GPU Initialization Failure Fallback Verification ---")
    results["gpu_fallback_verification"] = verify_gpu_fallback(iced_combined_exe)

    # Save results
    output_path = Path("prototyping/benchmarks/benchmark_results.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    print(f"\nAll benchmark results successfully recorded to {output_path}")
