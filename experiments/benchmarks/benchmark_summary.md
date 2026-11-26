# ReproForge Section 10 Controlled Benchmark Summary

- **Generated At:** `2026-11-26T07:02:21.208040+00:00`
- **Verdict Accuracy:** `10/10` (100.0%)
- **Diagnostic Category Accuracy:** `10/10` (100.0%)
- **Normalized Equivalence Recovery:** `3/3` (100.0%)
- **EXP-08 Safe Unknown Fallback:** `True`
- **Mean Diagnostic Latency:** `18.47 ms`
- **diffoscope Installed on Host:** `False`

| ID | Scenario | Expected Verdict | ReproForge Verdict | Categories | Latency (ms) | Category Hit |
| :--- | :--- | :--- | :--- | :--- | ---: | :---: |
| EXP-01 | Deterministic Build | `bit_for_bit_reproducible` | `bit_for_bit_reproducible` | `—` | 17.77 | PASS |
| EXP-02 | Timestamp Nondeterminism | `normalized_reproducible` | `normalized_reproducible` | `timestamp_nondeterminism` | 9.81 | PASS |
| EXP-03 | Dependency Variation | `non_reproducible` | `non_reproducible` | `dependency_variation` | 10.40 | PASS |
| EXP-04 | Environment Variable Variation | `non_reproducible` | `non_reproducible` | `environment_leakage` | 12.44 | PASS |
| EXP-05 | Toolchain Variation | `non_reproducible` | `non_reproducible` | `toolchain_variation` | 10.66 | PASS |
| EXP-06 | Archive Nondeterminism | `normalized_reproducible` | `normalized_reproducible` | `timestamp_nondeterminism, filesystem_ordering` | 12.70 | PASS |
| EXP-07 | Container Image Reproducibility | `non_reproducible` | `non_reproducible` | `base_image_variation` | 14.71 | PASS |
| EXP-08 | Unknown Nondeterminism | `inconclusive` | `inconclusive` | `unknown_variation` | 9.44 | PASS |
| EXP-09 | Cross-Environment Build | `non_reproducible` | `non_reproducible` | `platform_variation` | 8.93 | PASS |
| EXP-10 | Diagnostic Performance (60-file Archive) | `normalized_reproducible` | `normalized_reproducible` | `timestamp_nondeterminism, filesystem_ordering` | 77.80 | PASS |
