from __future__ import annotations

import html
import json
from pathlib import Path

from agent_shield.config.schema import (
    AssertionResult,
    RunReport,
    RunResult,
    StepResult,
    TestResult,
)


def write_html_report(
    report: RunReport,
    output_path: str,
    include_passing_runs: bool = False,
) -> Path:
    """Write a single self-contained HTML report.

    Hybrid rendering: the test list and per-test details are pre-rendered
    server-side so the report works in browsers without JavaScript or in
    contexts that strip JS (some email clients, CI artifact viewers). A
    small inline JS layer adds collapse/expand and run-status filtering
    on top.

    By default, repeated tests with high pass rates only show their failed
    runs to keep file size manageable. Pass `include_passing_runs=True` to
    keep every run in every test.
    """
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    body = _render_body(report, include_passing_runs)
    document = _HTML_TEMPLATE.format(
        title=html.escape(f"Agent Shield Report — {report.timestamp}"),
        css=_CSS,
        body=body,
        script=_JS,
    )
    path.write_text(document, encoding="utf-8")
    return path


# --- Body rendering ---


def _render_body(report: RunReport, include_passing_runs: bool) -> str:
    parts: list[str] = []
    parts.append(_render_summary(report))
    parts.append('<section class="tests">')
    for test in report.results:
        parts.append(_render_test(test, include_passing_runs))
    parts.append("</section>")
    return "\n".join(parts)


def _render_summary(report: RunReport) -> str:
    status_class = "pass" if report.passed else "fail"
    status_label = "PASSED" if report.passed else "FAILED"
    return f"""
<header class="summary">
  <h1>Agent Shield Report</h1>
  <div class="meta">
    <div><span class="label">Endpoint:</span> <code>{html.escape(report.agent_endpoint)}</code></div>
    <div><span class="label">Run ID:</span> <code>{html.escape(report.run_id)}</code></div>
    <div><span class="label">Timestamp:</span> {html.escape(report.timestamp)}</div>
    <div><span class="label">Duration:</span> {report.duration_ms / 1000:.1f}s</div>
  </div>
  <div class="overall {status_class}">
    <div class="score">{report.overall_score:.1f}%</div>
    <div class="status">{status_label}</div>
    <div class="threshold">threshold: {report.threshold}%</div>
  </div>
  <div class="counts">
    <span class="count pass">{report.passed_tests} passed</span>
    <span class="count fail">{report.failed_tests} failed</span>
    <span class="count total">{report.total_tests} total</span>
  </div>
</header>
"""


def _render_test(test: TestResult, include_passing_runs: bool) -> str:
    status_class = "pass" if test.passed else "fail"
    open_attr = "" if test.passed else " open"

    badge_text = ""
    if test.repeat > 1:
        badge_text = f"×{test.repeat} · consistency {test.consistency:.0f}%"
    elif test.type == "multi-turn":
        badge_text = "multi-turn"

    test_meta = f"""
    <span class="test-score">{test.score:.0f}%</span>
    {f'<span class="test-badge">{html.escape(badge_text)}</span>' if badge_text else ''}
    """

    runs_html = _render_runs(test, include_passing_runs)

    return f"""
<details class="test {status_class}"{open_attr}>
  <summary class="test-summary">
    <span class="test-status">{'✓' if test.passed else '✗'}</span>
    <span class="test-name">{html.escape(test.name)}</span>
    {test_meta}
  </summary>
  <div class="test-body">
    {runs_html}
  </div>
</details>
"""


def _render_runs(test: TestResult, include_passing_runs: bool) -> str:
    runs_to_show = list(test.runs)
    excluded_passing = 0

    if test.repeat > 1 and not include_passing_runs:
        # Default: show only failed runs (the interesting ones)
        kept = [r for r in runs_to_show if not r.passed]
        excluded_passing = len(runs_to_show) - len(kept)
        runs_to_show = kept

    parts: list[str] = []

    if test.repeat > 1:
        parts.append('<div class="filter-bar">')
        parts.append(
            f'<button type="button" class="filter active" data-filter="all">'
            f'All ({len(runs_to_show)})</button>'
        )
        parts.append(
            f'<button type="button" class="filter" data-filter="failed">'
            f'Failed only</button>'
        )
        parts.append(
            f'<button type="button" class="filter" data-filter="passed">'
            f'Passed only</button>'
        )
        if excluded_passing > 0:
            parts.append(
                f'<span class="filter-note">'
                f'{excluded_passing} passing runs hidden by default — '
                f're-run with --html-include-passing to keep them'
                f'</span>'
            )
        parts.append("</div>")

    parts.append('<div class="runs">')
    for run in runs_to_show:
        parts.append(_render_run(run, test.type))
    parts.append("</div>")

    return "".join(parts)


def _render_run(run: RunResult, test_type: str) -> str:
    status_class = "pass" if run.passed else "fail"
    glyph = "✓" if run.passed else "✗"

    steps_html = "".join(_render_step(s) for s in run.steps)

    return f"""
<details class="run {status_class}" data-status="{'passed' if run.passed else 'failed'}">
  <summary class="run-summary">
    <span class="run-glyph">{glyph}</span>
    <span class="run-label">Run {run.run}</span>
    <span class="run-score">{run.score:.0f}%</span>
    <span class="run-duration">{run.duration_ms}ms</span>
  </summary>
  <div class="steps">
    {steps_html}
  </div>
</details>
"""


def _render_step(step: StepResult) -> str:
    status_class = step.status
    glyph = {"passed": "✓", "failed": "✗", "skipped": "⊘"}.get(step.status, "?")

    response_block = ""
    if step.response and step.status != "skipped":
        response_block = (
            f'<div class="message assistant">'
            f'<span class="role">assistant</span>'
            f'<pre>{html.escape(step.response)}</pre>'
            f"</div>"
        )

    assertions_html = "".join(_render_assertion(a) for a in step.assertions)
    if assertions_html:
        assertions_block = (
            f'<div class="assertions"><div class="label">Assertions</div>'
            f"{assertions_html}</div>"
        )
    else:
        assertions_block = ""

    skipped_note = ""
    if step.status == "skipped":
        skipped_note = (
            '<div class="skipped-note">Skipped after earlier failure (on_step_fail: stop)</div>'
        )

    return f"""
<div class="step {status_class}">
  <div class="step-header">
    <span class="step-glyph">{glyph}</span>
    <span class="step-label">Step {step.step}</span>
    <span class="step-score">{step.score:.0f}%</span>
  </div>
  <div class="message user">
    <span class="role">user</span>
    <pre>{html.escape(step.prompt)}</pre>
  </div>
  {response_block}
  {assertions_block}
  {skipped_note}
</div>
"""


def _render_assertion(assertion: AssertionResult) -> str:
    status_class = "pass" if assertion.passed else "fail"
    glyph = "✓" if assertion.passed else "✗"
    label = assertion.type
    if assertion.mode:
        label = f"{label}({assertion.mode})"
    return f"""
<div class="assertion {status_class}">
  <span class="glyph">{glyph}</span>
  <span class="type">{html.escape(label)}</span>
  <span class="detail">{html.escape(assertion.detail)}</span>
</div>
"""


# --- Static template / CSS / JS ---

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{css}</style>
</head>
<body>
{body}
<script>{script}</script>
</body>
</html>
"""

_CSS = """
* { box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  margin: 0;
  padding: 24px;
  background: #f7f8fa;
  color: #1a1a1a;
  line-height: 1.5;
}
header.summary {
  background: white;
  border-radius: 8px;
  padding: 24px;
  margin-bottom: 24px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.06);
}
header.summary h1 { margin: 0 0 16px; font-size: 24px; }
.meta { display: flex; flex-wrap: wrap; gap: 12px 24px; font-size: 14px; color: #555; margin-bottom: 16px; }
.meta .label { font-weight: 600; color: #333; }
.meta code { background: #f0f1f3; padding: 1px 6px; border-radius: 3px; }
.overall { display: flex; align-items: baseline; gap: 16px; padding: 16px; border-radius: 6px; margin-bottom: 12px; }
.overall.pass { background: #e6f7ea; color: #1a6b2e; }
.overall.fail { background: #fdecec; color: #a4181c; }
.overall .score { font-size: 36px; font-weight: 700; }
.overall .status { font-size: 18px; font-weight: 600; }
.overall .threshold { font-size: 14px; opacity: 0.8; margin-left: auto; }
.counts { display: flex; gap: 12px; font-size: 14px; }
.count { padding: 4px 12px; border-radius: 12px; font-weight: 600; }
.count.pass { background: #e6f7ea; color: #1a6b2e; }
.count.fail { background: #fdecec; color: #a4181c; }
.count.total { background: #eef0f4; color: #555; }
.tests { display: flex; flex-direction: column; gap: 8px; }
.test {
  background: white;
  border-radius: 6px;
  border-left: 4px solid;
  box-shadow: 0 1px 2px rgba(0,0,0,0.04);
}
.test.pass { border-color: #29a04a; }
.test.fail { border-color: #d33a3f; }
.test-summary {
  cursor: pointer;
  padding: 14px 18px;
  display: flex;
  align-items: center;
  gap: 12px;
  font-weight: 500;
  list-style: none;
}
.test-summary::-webkit-details-marker { display: none; }
.test-status { font-size: 16px; font-weight: 700; }
.test.pass .test-status { color: #29a04a; }
.test.fail .test-status { color: #d33a3f; }
.test-name { flex: 1; }
.test-score { font-weight: 700; }
.test-badge { font-size: 12px; padding: 2px 8px; background: #eef0f4; border-radius: 10px; color: #555; }
.test-body { padding: 0 18px 18px 18px; }
.filter-bar { display: flex; gap: 8px; align-items: center; margin-bottom: 12px; flex-wrap: wrap; }
.filter {
  background: #eef0f4;
  border: 1px solid transparent;
  padding: 4px 10px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 13px;
  color: #333;
}
.filter.active { background: #1a73e8; color: white; }
.filter-note { font-size: 12px; color: #777; font-style: italic; }
.runs { display: flex; flex-direction: column; gap: 6px; }
.run {
  background: #fafbfc;
  border-radius: 4px;
  border-left: 3px solid;
}
.run.pass { border-color: #29a04a; }
.run.fail { border-color: #d33a3f; }
.run-summary {
  cursor: pointer;
  padding: 8px 12px;
  display: flex;
  gap: 12px;
  align-items: center;
  font-size: 13px;
  list-style: none;
}
.run-summary::-webkit-details-marker { display: none; }
.run.pass .run-glyph { color: #29a04a; }
.run.fail .run-glyph { color: #d33a3f; }
.run-glyph { font-weight: 700; }
.run-label { flex: 1; font-weight: 500; }
.run-score, .run-duration { color: #777; }
.steps { padding: 0 12px 12px 12px; display: flex; flex-direction: column; gap: 12px; }
.step {
  border: 1px solid #e6e8eb;
  border-radius: 4px;
  padding: 10px;
  background: white;
}
.step.failed { border-color: #f0c2c4; background: #fff8f8; }
.step.skipped { opacity: 0.6; }
.step-header {
  display: flex; gap: 8px; align-items: center;
  font-size: 12px; font-weight: 600; color: #555;
  margin-bottom: 8px;
}
.step.passed .step-glyph { color: #29a04a; }
.step.failed .step-glyph { color: #d33a3f; }
.step.skipped .step-glyph { color: #c79a13; }
.message { margin-top: 6px; }
.message .role { font-size: 11px; font-weight: 700; text-transform: uppercase; color: #888; }
.message pre {
  margin: 4px 0 0;
  padding: 8px 10px;
  background: #f4f6f8;
  border-radius: 3px;
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
}
.message.user pre { background: #eef5ff; }
.message.assistant pre { background: #f4f6f8; }
.assertions { margin-top: 8px; padding-top: 8px; border-top: 1px solid #eef0f4; }
.assertions .label { font-size: 11px; font-weight: 700; text-transform: uppercase; color: #888; margin-bottom: 4px; }
.assertion { display: flex; gap: 8px; font-size: 12px; padding: 2px 0; }
.assertion .type { font-weight: 600; }
.assertion.pass .glyph { color: #29a04a; }
.assertion.fail .glyph { color: #d33a3f; }
.skipped-note { font-size: 12px; font-style: italic; color: #888; margin-top: 6px; }
"""

_JS = """
document.addEventListener('click', function (e) {
  if (!e.target.matches('.filter')) return;
  const button = e.target;
  const bar = button.parentElement;
  const filter = button.dataset.filter;
  bar.querySelectorAll('.filter').forEach(b => b.classList.toggle('active', b === button));
  const runs = bar.parentElement.querySelectorAll('.runs > .run');
  runs.forEach(run => {
    if (filter === 'all') {
      run.style.display = '';
    } else {
      run.style.display = run.dataset.status === filter ? '' : 'none';
    }
  });
});
"""
