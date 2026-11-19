"""Permanent Slint Desktop Application Benchmark Harness.

Evaluates the actual compiled permanent desktop client (reproforge_desktop.exe):
1. Release executable binary size.
2. Hardware Accelerated Mode (Femtovg/Skia): First-frame presentation startup & idle footprint.
3. Software Rasterizer Mode (--renderer=software): First-frame presentation startup & idle footprint.
4. Large-scale dataset scaling at N=200, N=10,000, and N=50,000 records.
"""

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
import psutil


def measure_binary_size(exe_path: Path) -> float:
    return round(os.path.getsize(exe_path) / (1024 * 1024), 2)


def measure_first_frame(exe_path: Path, args: Optional[List[str]] = None, env: Optional[Dict[str, str]] = None, iterations: int = 3) -> Dict[str, float]:
    durations: List[float] = []
    cmd = [str(exe_path)]
    if args:
        cmd.extend(args)

    for _ in range(iterations):
        t0 = time.perf_counter()
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env)
        try:
            while True:
                if time.perf_counter() - t0 > 10.0:
                    break
                line = p.stdout.readline()
                if not line:
                    break
                if "FIRST_FRAME_PRESENTED" in line:
                    durations.append((time.perf_counter() - t0) * 1000.0)
                    break
        finally:
            try:
                p.kill()
                p.wait(timeout=1.0)
            except Exception:
                pass
            time.sleep(0.1)

    if not durations:
        return {"avg_ms": 0.0, "min_ms": 0.0, "max_ms": 0.0}

    return {
        "avg_ms": round(sum(durations) / len(durations), 1),
        "min_ms": round(min(durations), 1),
        "max_ms": round(max(durations), 1),
    }


def measure_idle(exe_path: Path, args: Optional[List[str]] = None, env: Optional[Dict[str, str]] = None, duration_seconds: int = 3) -> Dict[str, Any]:
    cmd = [str(exe_path)]
    if args:
        cmd.extend(args)

    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env)
    proc = psutil.Process(p.pid)
    time.sleep(1.5)

    rss_samples: List[float] = []
    cpu_samples: List[float] = []

    try:
        start = time.time()
        try:
            proc.cpu_percent(interval=None)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

        while time.time() - start < duration_seconds:
            try:
                rss_samples.append(proc.memory_info().rss / (1024 * 1024))
                cpu_samples.append(proc.cpu_percent(interval=0.5))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                break
    finally:
        try:
            p.kill()
            p.wait(timeout=1.0)
        except Exception:
            pass

    return {
        "avg_ram_mb": round(sum(rss_samples) / len(rss_samples), 2) if rss_samples else 0.0,
        "avg_cpu_percent": round(sum(cpu_samples) / len(cpu_samples), 2) if cpu_samples else 0.0,
    }


def run_configuration(name: str, exe_path: Path, args: Optional[List[str]] = None, n_records: int = 200) -> Dict[str, Any]:
    env = os.environ.copy()
    env["REPROFORGE_BENCH_RECORDS"] = str(n_records)

    startup = measure_first_frame(exe_path, args=args, env=env, iterations=3)
    idle = measure_idle(exe_path, args=args, env=env, duration_seconds=3)

    print(f"[{name:<32} | N={n_records:>5}] First-Frame: {startup['avg_ms']:>6.1f} ms | Idle RAM: {idle['avg_ram_mb']:>6.2f} MB | CPU: {idle['avg_cpu_percent']:>4.1f}%")
    return {
        "name": name,
        "records": n_records,
        "first_frame_ms": startup,
        "idle_telemetry": idle,
    }


if __name__ == "__main__":
    exe = Path("desktop/target/release/reproforge_desktop.exe")
    assert exe.exists(), f"Release binary not found at {exe}"

    size_mb = measure_binary_size(exe)
    print(f"\n--- ReproForge Permanent Desktop Client ({exe.name}) ---")
    print(f"Binary Size on Disk: {size_mb} MB")

    results = {
        "binary_size_mb": size_mb,
        "benchmarks": {}
    }

    # 1. Baseline 200 records (Hardware vs Software)
    print("\n1. Rendering Pipeline Comparison (N=200 records):")
    results["benchmarks"]["hardware_200"] = run_configuration("Slint Permanent (Hardware)", exe, args=None, n_records=200)
    results["benchmarks"]["software_200"] = run_configuration("Slint Permanent (Software)", exe, args=["--renderer=software"], n_records=200)

    # 2. Tabular Scaling (Hardware)
    print("\n2. Tabular Scaling in Permanent Application:")
    results["benchmarks"]["hardware_10000"] = run_configuration("Slint Permanent (Hardware)", exe, args=None, n_records=10000)
    results["benchmarks"]["hardware_50000"] = run_configuration("Slint Permanent (Hardware)", exe, args=None, n_records=50000)

    # Save
    out = Path("desktop/benchmarks/desktop_benchmark_results.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"\nPermanent desktop benchmark results saved to {out}")
