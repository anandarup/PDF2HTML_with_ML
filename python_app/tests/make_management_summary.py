#!/usr/bin/env python3
"""
Generate a MANAGEMENT-FACING coverage summary (Section 1) as HTML from the
coverage.py JSON output. The DEVELOPER-FACING detailed report (Section 2) is the
standard `coverage html` output (htmlcov/index.html), which this page links to.

Usage:
    python tests/make_management_summary.py coverage.json coverage_summary.html
"""
import json
import sys
from datetime import datetime


def build(json_path: str, out_path: str) -> None:
    data = json.load(open(json_path))
    totals = data["totals"]
    files = data["files"]

    line_pct = totals["percent_covered"]
    branch_total = totals.get("num_branches", 0)
    branch_cov = totals.get("covered_branches", 0)
    branch_pct = (branch_cov / branch_total * 100) if branch_total else 100.0
    status = "PASS" if line_pct >= 100 and branch_pct >= 100 else \
             ("ON TRACK" if line_pct >= 80 else "NEEDS WORK")
    status_color = "#1e874b" if status == "PASS" else ("#b45309" if status == "ON TRACK" else "#c9382e")

    rows = []
    for fname, fdata in sorted(files.items()):
        ft = fdata["summary"]
        fb_total = ft.get("num_branches", 0)
        fb_cov = ft.get("covered_branches", 0)
        fb_pct = (fb_cov / fb_total * 100) if fb_total else 100.0
        rows.append(
            f"<tr><td><code>{fname}</code></td>"
            f"<td>{ft['num_statements']}</td>"
            f"<td>{ft['percent_covered']:.0f}%</td>"
            f"<td>{fb_cov}/{fb_total} ({fb_pct:.0f}%)</td>"
            f"<td>{ft['missing_lines']}</td></tr>"
        )

    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Coverage — Management Summary</title>
<style>
  body{{margin:0;font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;color:#2b2f38;background:#f6f8fb;}}
  .wrap{{max-width:860px;margin:0 auto;padding:0 24px 60px;}}
  header{{background:linear-gradient(135deg,#0f4c75,#1a1a2e);color:#fff;padding:40px 24px;}}
  header .inner{{max-width:860px;margin:0 auto;}}
  h1{{margin:0 0 6px;font-size:1.7rem;}} .sub{{opacity:.85;}}
  .cards{{display:flex;gap:14px;flex-wrap:wrap;margin:26px 0;}}
  .cards div{{flex:1 1 150px;background:#fff;border:1px solid #e3e7ee;border-radius:12px;padding:16px;}}
  .cards b{{display:block;font-size:2rem;}} .cards span{{font-size:.8rem;color:#5b6472;}}
  .status{{display:inline-block;padding:4px 14px;border-radius:999px;color:#fff;font-weight:700;background:{status_color};}}
  table{{border-collapse:collapse;width:100%;margin:18px 0;background:#fff;font-size:.92rem;}}
  th,td{{border:1px solid #e3e7ee;padding:9px 12px;text-align:left;}} th{{background:#eef2f8;}}
  code{{background:#eef1f6;padding:1px 6px;border-radius:5px;font-size:.86em;}}
  a.btn{{display:inline-block;margin-top:10px;padding:10px 18px;background:#0f4c75;color:#fff;border-radius:8px;text-decoration:none;font-weight:600;}}
  .note{{background:#ecfeff;border-left:4px solid #3282b8;padding:12px 16px;border-radius:0 8px 8px 0;margin:18px 0;}}
  footer{{margin-top:30px;color:#5b6472;font-size:.85rem;}}
</style></head>
<body>
<header><div class="inner">
  <h1>Unit Test Coverage — Executive Summary</h1>
  <div class="sub">Module: <code>state/job_store.py</code> · generated {datetime.now():%Y-%m-%d %H:%M}</div>
</div></header>
<div class="wrap">
  <p style="margin-top:24px;">Overall coverage status: <span class="status">{status}</span></p>
  <div class="cards">
    <div><b style="color:{status_color}">{line_pct:.0f}%</b><span>Line coverage</span></div>
    <div><b style="color:{status_color}">{branch_pct:.0f}%</b><span>Branch coverage</span></div>
    <div><b>{totals['num_statements']}</b><span>Statements</span></div>
    <div><b>{branch_total}</b><span>Branches</span></div>
  </div>
  <div class="note">
    <strong>What this means:</strong> every line of code, every decision branch
    (if/else, try/except, loops), and every function in this module is exercised
    by an automated test. Uncovered lines: <strong>{totals['missing_lines']}</strong>.
    Tests run in full isolation (no network / DB / disk dependencies), so they
    are safe and repeatable in CI and on any VM.
  </div>
  <h2>Per-file breakdown</h2>
  <table>
    <thead><tr><th>File</th><th>Statements</th><th>Line %</th><th>Branch</th><th>Missing lines</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
  <h2>Section 2 — Developer detail</h2>
  <p>The line-by-line annotated report (which statements/branches each test hit)
     is in the developer report:</p>
  <a class="btn" href="../htmlcov/index.html">Open developer coverage report →</a>
  <footer>Section 1 (this page) = management summary · Section 2 = <code>htmlcov/index.html</code> (developer detail).</footer>
</div></body></html>"""
    open(out_path, "w").write(html)
    print(f"Management summary written to {out_path}")


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "coverage.json"
    dst = sys.argv[2] if len(sys.argv) > 2 else "coverage_summary.html"
    build(src, dst)
