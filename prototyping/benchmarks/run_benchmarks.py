"""Empirical release benchmark harness for Slint vs. Iced desktop prototypes.

Measures:
1. Release executable binary size on disk.
2. Cold startup time (process spawn to first steady-state memory reading, 5 iterations).
3. Idle Working Set RAM (RSS) over 30-second steady-state window.
4. Idle CPU utilization percentage over 30-second steady-state window.
"""

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List
import psutil


def measure_binary_size(exe_path: Path) -> float:
    """Return binary size in megabytes."""
    return round(os.path.getsize(exe_path) / (1024 * 1024), 2)


def measure_cold_startup(exe_path: Path, iterations: int = 5) -> Dict[str, float]:
    """Measure cold process launch to working set stabilization time across multiple runs."""
    durations: List[float] = []

    for _ in range(iterations):
        t0 = time.perf_counter()
        proc = subprocess.Popen([str(exe_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            p = psutil.Process(proc.pid)
            # Wait until process allocates initial GUI working set (> 5MB)
            while True:
                if time.perf_counter() - t0 > 10.0:
                    break
                try:
                    mem = p.memory_info().rss
                    if mem > 5 * 1024 * 1024:
                        break
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    break
                time.sleep(0.005)

            durations.append((time.perf_counter() - t0) * 1000.0)  # ms
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()
            time.sleep(0.2)

    avg_startup = round(sum(durations) / len(durations), 1)
    min_startup = round(min(durations), 1)
    max_startup = round(max(durations), 1)
    return {"avg_ms": avg_startup, "min_ms": min_startup, "max_ms": max_startup}


def measure_idle_footprint(exe_path: Path, duration_seconds: int = 30) -> Dict[str, Any]:
    """Measure steady-state idle RAM and CPU utilization over a timed duration."""
    proc = subprocess.Popen([str(exe_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    p = psutil.Process(proc.pid)

    # Let the UI stabilize for 2 seconds
    time.sleep(2.0)

    rss_samples: List[float] = []
    cpu_samples: List[float] = []

    try:
        start_time = time.time()
        # Initialize CPU percentage baseline
        p.cpu_percent(interval=None)

        while time.time() - start_time < duration_seconds:
            try:
                mem_mb = p.memory_info().rss / (1024 * 1024)
                rss_samples.append(mem_mb)
                cpu = p.cpu_percent(interval=1.0)
                cpu_samples.append(cpu)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                break

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            proc.kill()

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


def run_benchmark(framework_name: str, exe_path: Path, duration_seconds: int = 30) -> Dict[str, Any]:
    """Execute complete benchmark suite for a candidate framework binary."""
    print(f"\n--- Benchmarking {framework_name} Release Binary ---")
    print(f"Path: {exe_path}")

    size_mb = measure_binary_size(exe_path)
    print(f"1. Binary Size: {size_mb} MB")

    print(f"2. Measuring cold startup over 5 runs...")
    startup = measure_cold_startup(exe_path, iterations=5)
    print(f"   Average Startup: {startup['avg_ms']} ms (min: {startup['min_ms']} ms, max: {startup['max_ms']} ms)")

    print(f"3. Measuring steady-state idle over {duration_seconds}s...")
    idle = measure_idle_footprint(exe_path, duration_seconds=duration_seconds)
    print(f"   Average Idle RAM: {idle['avg_ram_mb']} MB (peak: {idle['max_ram_mb']} MB)")
    print(f"   Average Idle CPU: {idle['avg_cpu_percent']}%")

    return {
        "framework": framework_name,
        "binary_size_mb": size_mb,
        "startup_ms": startup,
        "idle_telemetry": idle,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="ReproForge UI Benchmark Harness")
    parser.add_argument("--slint-exe", type=Path, help="Path to Slint release binary")
    parser.add_argument("--iced-exe", type=Path, help="Path to Iced (wgpu) release binary")
    parser.add_argument("--iced-skia-exe", type=Path, help="Path to Iced (tiny-skia) release binary")
    parser.add_argument("--duration", type=int, default=15, help="Idle sampling duration in seconds")
    parser.add_argument("--output-json", type=Path, default=Path("prototyping/benchmarks/benchmark_results.json"))

    args = parser.parse_args()
    results = {}
    if args.output_json.exists():
        try:
            with open(args.output_json, "r", encoding="utf-8") as f:
                results = json.load(f)
        except Exception:
            results = {}

    if args.slint_exe and args.slint_exe.exists():
        results["slint"] = run_benchmark("Slint", args.slint_exe, duration_seconds=args.duration)

    if args.iced_exe and args.iced_exe.exists():
        results["iced_wgpu"] = run_benchmark("Iced (wgpu)", args.iced_exe, duration_seconds=args.duration)

    if args.iced_skia_exe and args.iced_skia_exe.exists():
        results["iced_tiny_skia"] = run_benchmark("Iced (tiny-skia)", args.iced_skia_exe, duration_seconds=args.duration)

    if results:
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)
        print(f"\nBenchmark results saved to {args.output_json}")
