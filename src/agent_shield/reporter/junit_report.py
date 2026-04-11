from __future__ import annotations

from pathlib import Path
from xml.etree.ElementTree import Element, ElementTree, SubElement

from agent_shield.config.schema import RunReport, RunResult, TestResult


def write_junit_report(report: RunReport, output_path: str) -> Path:
    """Write a JUnit-compatible XML report to `output_path`.

    One <testcase> per agent-shield test (not per repeat run — that matches
    the standard JUnit convention used by GitHub Actions, GitLab CI, and
    most CI report viewers). Failure body shows aggregate stats and a
    sample of what failed.
    """
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    total = report.total_tests
    failures = report.failed_tests
    duration_s = report.duration_ms / 1000.0

    root = Element(
        "testsuites",
        {
            "name": "agent-shield",
            "tests": str(total),
            "failures": str(failures),
            "time": f"{duration_s:.3f}",
        },
    )
    suite = SubElement(
        root,
        "testsuite",
        {
            "name": "agent-shield",
            "tests": str(total),
            "failures": str(failures),
            "time": f"{duration_s:.3f}",
            "timestamp": report.timestamp,
            "hostname": report.agent_endpoint,
        },
    )

    for test in report.results:
        _append_testcase(suite, test)

    tree = ElementTree(root)
    tree.write(path, encoding="utf-8", xml_declaration=True)
    return path


def _append_testcase(suite: Element, test: TestResult) -> None:
    duration_s = test.duration_ms / 1000.0
    case = SubElement(
        suite,
        "testcase",
        {
            "classname": "agent-shield",
            "name": test.name,
            "time": f"{duration_s:.3f}",
        },
    )

    if test.passed:
        return

    # Build a human-readable failure body
    if test.repeat > 1:
        message = (
            f"{test.pass_rate:.0f}% pass rate "
            f"({sum(1 for r in test.runs if r.passed)}/{test.repeat} runs)"
        )
    else:
        message = f"{test.score:.0f}% score"

    body = _format_failure_body(test)

    failure = SubElement(case, "failure", {"message": message, "type": "AssertionFailure"})
    failure.text = body


def _format_failure_body(test: TestResult) -> str:
    """Build the text shown inside <failure>...</failure>."""
    lines: list[str] = []

    failed_runs = [r for r in test.runs if not r.passed]
    if test.repeat > 1:
        lines.append(
            f"Test '{test.name}' failed in "
            f"{len(failed_runs)}/{test.repeat} runs "
            f"(consistency: {test.consistency:.0f}%)."
        )
        lines.append("")

    sample = failed_runs[0] if failed_runs else test.runs[0]
    if test.repeat > 1:
        lines.append(f"Sample failure (run {sample.run}):")
        lines.append("")

    _format_run(lines, sample)
    return "\n".join(lines)


def _format_run(lines: list[str], run: RunResult) -> None:
    is_multi_step = len(run.steps) > 1
    for step in run.steps:
        if is_multi_step:
            lines.append(f"Step {step.step} [{step.status}]: {step.prompt!r}")
        if step.status == "skipped":
            lines.append("  (skipped)")
            continue
        if step.response:
            response_preview = step.response.replace("\n", " ")
            if len(response_preview) > 200:
                response_preview = response_preview[:197] + "..."
            lines.append(f"  Response: {response_preview}")
        for assertion in step.assertions:
            if assertion.passed:
                continue
            label = assertion.type
            if assertion.mode:
                label = f"{label}({assertion.mode})"
            lines.append(f"  FAIL {label}: {assertion.detail}")
