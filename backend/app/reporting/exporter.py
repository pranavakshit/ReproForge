from __future__ import annotations

import csv
import html
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


class ReportExporter:
    """Exports structured reproducibility, diagnostic, and benchmark reports (JSON, CSV, HTML)."""

    @staticmethod
    def comparison_to_json(details: Dict[str, Any]) -> str:
        payload = {
            "report_format": "reproforge-comparison-report-v1.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            **details,
        }
        return json.dumps(payload, indent=2, default=str)

    @staticmethod
    def comparison_to_csv(details: Dict[str, Any]) -> str:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "comparison_id",
            "verdict",
            "cri",
            "record_kind",
            "path_or_category",
            "type_or_severity",
            "status_or_epistemic",
            "detail_or_summary",
        ])
        cid = details.get("id", "")
        verdict = details.get("verdict", "")
        score = details.get("score") or {}
        cri = score.get("composite_reproducibility_index", details.get("composite_reproducibility_index", ""))

        for entry in details.get("diff_entries", []):
            writer.writerow([
                cid,
                verdict,
                cri,
                "DIFF",
                entry.get("relative_path", ""),
                entry.get("diff_type", ""),
                f"{entry.get('size_a', 0)}->{entry.get('size_b', 0)}",
                (entry.get("diff_snippet") or "")[:200],
            ])

        for diag in details.get("diagnostics", details.get("diagnostic_findings", [])):
            writer.writerow([
                cid,
                verdict,
                cri,
                "DIAGNOSTIC",
                diag.get("category", ""),
                diag.get("confidence", ""),
                diag.get("epistemic_status", ""),
                diag.get("root_cause_summary", diag.get("summary", "")),
            ])

        return buf.getvalue()

    @staticmethod
    def comparison_to_html(details: Dict[str, Any]) -> str:
        cid = html.escape(str(details.get("id", "unknown")))
        b1 = html.escape(str(details.get("build_id_1", details.get("build_a_id", "—"))))
        b2 = html.escape(str(details.get("build_id_2", details.get("build_b_id", "—"))))
        verdict = html.escape(str(details.get("verdict", "UNCOMPARED")))
        score = details.get("score") or {}
        c_ratio = float(score.get("content_agreement_ratio", details.get("content_agreement_ratio", 0.0)))
        m_ratio = float(score.get("metadata_agreement_ratio", details.get("metadata_agreement_ratio", 0.0)))
        e_ratio = float(score.get("environment_consistency_score", details.get("environment_consistency_score", 0.0)))
        cri = float(score.get("composite_reproducibility_index", details.get("composite_reproducibility_index", 0.0)))

        diff_rows = []
        for entry in details.get("diff_entries", []):
            diff_rows.append(
                "<tr>"
                f"<td><code>{html.escape(str(entry.get('relative_path', '')))}</code></td>"
                f"<td>{html.escape(str(entry.get('diff_type', '')))}</td>"
                f"<td>{int(entry.get('size_a', 0))} B &rarr; {int(entry.get('size_b', 0))} B</td>"
                "</tr>"
            )
        diff_table = "".join(diff_rows) or '<tr><td colspan="3">No file differences detected.</td></tr>'

        diag_rows = []
        for diag in details.get("diagnostics", details.get("diagnostic_findings", [])):
            diag_rows.append(
                "<tr>"
                f"<td><strong>{html.escape(str(diag.get('category', '')))}</strong></td>"
                f"<td>{html.escape(str(diag.get('epistemic_status', 'INFERRED')))}</td>"
                f"<td>{html.escape(str(diag.get('confidence', '')))}</td>"
                f"<td>{html.escape(str(diag.get('root_cause_summary', diag.get('summary', ''))))}</td>"
                f"<td>{html.escape(str(diag.get('suggested_remediation', '')))}</td>"
                "</tr>"
            )
        diag_table = "".join(diag_rows) or '<tr><td colspan="5">No diagnostic findings triggered.</td></tr>'

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>ReproForge Comparison Report — {cid}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #090D16; color: #F1F5F9; margin: 0; padding: 32px; }}
  .card {{ background: #121A2A; border: 1px solid #1D283D; border-radius: 10px; padding: 20px; margin-bottom: 20px; }}
  h1, h2 {{ margin-top: 0; color: #F8FAFC; }}
  .badge {{ display: inline-block; padding: 4px 10px; border-radius: 999px; font-size: 12px; font-weight: 700; background: #13284A; color: #93C5FD; border: 1px solid #3B82F6; }}
  .grid {{ display: grid; grid-template-columns: repeat(4, minmax(140px, 1fr)); gap: 12px; margin-top: 14px; }}
  .metric {{ background: #0D1320; border: 1px solid #1D283D; border-radius: 8px; padding: 12px; }}
  .metric span {{ display: block; font-size: 11px; color: #94A3B8; text-transform: uppercase; }}
  .metric strong {{ font-size: 20px; color: #22D3EE; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
  th, td {{ text-align: left; padding: 9px 10px; border-bottom: 1px solid #1D283D; font-size: 13px; }}
  th {{ color: #94A3B8; font-size: 11px; text-transform: uppercase; }}
  code {{ color: #93C5FD; }}
</style>
</head>
<body>
  <div class="card">
    <h1>ReproForge Comparison Report</h1>
    <p>Comparison ID: <code>{cid}</code> &nbsp;|&nbsp; Build A: <code>{b1}</code> &nbsp;|&nbsp; Build B: <code>{b2}</code></p>
    <span class="badge">Verdict: {verdict}</span>
    <div class="grid">
      <div class="metric"><span>Content Agreement</span><strong>{c_ratio:.1%}</strong></div>
      <div class="metric"><span>Metadata Agreement</span><strong>{m_ratio:.1%}</strong></div>
      <div class="metric"><span>Env Consistency</span><strong>{e_ratio:.1%}</strong></div>
      <div class="metric"><span>Composite CRI</span><strong>{cri:.3f}</strong></div>
    </div>
  </div>
  <div class="card">
    <h2>Artifact Differences</h2>
    <table><thead><tr><th>Relative Path</th><th>Diff Type</th><th>Size Delta</th></tr></thead><tbody>{diff_table}</tbody></table>
  </div>
  <div class="card">
    <h2>Root-Cause Diagnostics</h2>
    <table><thead><tr><th>Category</th><th>Epistemic</th><th>Confidence</th><th>Summary</th><th>Remediation</th></tr></thead><tbody>{diag_table}</tbody></table>
  </div>
</body>
</html>
"""

    @staticmethod
    def write_report(content: str, output_path: Path) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")
        return output_path

    @staticmethod
    def experiments_to_csv(scenarios: List[Dict[str, Any]]) -> str:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "experiment_id",
            "name",
            "expected_verdict",
            "reproforge_verdict",
            "expected_category",
            "detected_categories",
            "category_match",
            "latency_ms",
            "sha_baseline_blind_spot",
            "raw_diff_blind_spot",
        ])
        for s in scenarios:
            writer.writerow([
                s.get("experiment_id", ""),
                s.get("name", ""),
                s.get("expected_verdict", ""),
                s.get("reproforge_verdict", ""),
                s.get("expected_category", ""),
                "|".join(s.get("detected_categories", [])),
                s.get("category_match", False),
                s.get("latency_ms", 0.0),
                s.get("sha_baseline_blind_spot", ""),
                s.get("raw_diff_blind_spot", ""),
            ])
        return buf.getvalue()
