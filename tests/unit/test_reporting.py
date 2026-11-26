import csv
import io
import json
from pathlib import Path

from backend.app.reporting.exporter import ReportExporter


def test_report_exporter_json_csv_html(tmp_path: Path):
    details = {
        "id": "cmp-rep-1",
        "build_id_1": "bld-a",
        "build_id_2": "bld-b",
        "verdict": "normalized_reproducible",
        "score": {
            "content_agreement_ratio": 1.0,
            "metadata_agreement_ratio": 0.85,
            "environment_consistency_score": 1.0,
            "composite_reproducibility_index": 0.94,
        },
        "diff_entries": [
            {
                "relative_path": "pkg/mod.py",
                "diff_type": "metadata",
                "size_a": 120,
                "size_b": 120,
                "diff_snippet": "mtime 1700000000 != 1760000000",
            }
        ],
        "diagnostics": [
            {
                "category": "timestamp_nondeterminism",
                "confidence": "high",
                "epistemic_status": "OBSERVED",
                "root_cause_summary": "Header timestamp variance <script>alert(1)</script>",
                "suggested_remediation": "Pin SOURCE_DATE_EPOCH",
            }
        ],
    }

    j = json.loads(ReportExporter.comparison_to_json(details))
    assert j["report_format"] == "reproforge-comparison-report-v1.0"
    assert j["id"] == "cmp-rep-1"

    c = ReportExporter.comparison_to_csv(details)
    rows = list(csv.reader(io.StringIO(c)))
    assert len(rows) == 3  # header + 1 diff + 1 diagnostic

    h = ReportExporter.comparison_to_html(details)
    assert "ReproForge Comparison Report" in h
    assert "<script>alert(1)</script>" not in h
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in h

    out = ReportExporter.write_report(h, tmp_path / "reports" / "report.html")
    assert out.exists()
