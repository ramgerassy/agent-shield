from __future__ import annotations

from collections import Counter
from pathlib import Path

from agent_shield.config.schema import (
    AssertionResult,
    RunReport,
    RunResult,
    StepResult,
    TestResult,
)


def write_markdown_report(report: RunReport, output_path: str) -> Path:
    """Write a markdown report intended for an AI agent (or human) to read.

    The structure is failure-centric: lead with overall stats, then each
    failed test with full conversation history, the failing assertion(s),
    and the response that triggered them. Passed tests get a short list
    at the end. The goal is to give an LLM (or human) the smallest
    sufficient context to suggest a fix.
    """
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    _write_header(lines, report)
    _write_failed_tests(lines, report)
    _write_passed_tests(lines, report)
    _write_footer(lines, report)

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_header(lines: list[str], report: RunReport) -> None:
    status = "PASSED" if report.passed else "FAILED"
    lines.append(f"# Agent Shield Report")
    lines.append("")
    lines.append(f"- **Run ID:** `{report.run_id}`")
    lines.append(f"- **Timestamp:** {report.timestamp}")
    lines.append(f"- **Endpoint:** `{report.agent_endpoint}`")
    lines.append(
        f"- **Overall:** **{report.overall_score:.1f}%** "
        f"({status}, threshold {report.threshold}%)"
    )
    lines.append(
        f"- **Tests:** {report.passed_tests} passed, "
        f"{report.failed_tests} failed (of {report.total_tests} total)"
    )
    lines.append(f"- **Duration:** {report.duration_ms / 1000:.1f}s")
    lines.append("")


def _write_failed_tests(lines: list[str], report: RunReport) -> None:
    failed = [t for t in report.results if not t.passed]
    if not failed:
        return

    lines.append(f"## Failed tests ({len(failed)})")
    lines.append("")
    lines.append(
        "_The sections below are intended for an AI agent or developer "
        "to read and reason about. Each failed test includes its full "
        "context: prompt(s), response(s), and the assertion(s) that did "
        "not match._"
    )
    lines.append("")

    for test in failed:
        _write_failed_test(lines, test)


def _write_failed_test(lines: list[str], test: TestResult) -> None:
    failed_runs = [r for r in test.runs if not r.passed]
    lines.append(f"### {test.name}")
    lines.append("")
    lines.append(f"- **Type:** {test.type}")
    lines.append(f"- **Score:** {test.score:.0f}%")

    if test.repeat > 1:
        lines.append(
            f"- **Pass rate:** {test.pass_rate:.0f}% "
            f"({len(failed_runs)}/{test.repeat} runs failed)"
        )
        lines.append(
            f"- **Consistency:** {test.consistency:.0f}% "
            f"(how often the test had the same outcome)"
        )

    # Pattern detection across failed runs in multi-turn tests
    if test.type == "multi-turn" and len(failed_runs) > 1:
        pattern = _detect_failure_pattern(failed_runs)
        if pattern:
            lines.append(f"- **Failure pattern:** {pattern}")

    lines.append("")

    sample = failed_runs[0]
    if test.repeat > 1:
        lines.append(f"#### Sample failure (run {sample.run} of {test.repeat})")
    else:
        lines.append("#### Failure detail")
    lines.append("")

    _write_run_detail(lines, sample)
    lines.append("")


def _write_run_detail(lines: list[str], run: RunResult) -> None:
    is_multi_step = len(run.steps) > 1

    if is_multi_step:
        # Show full conversation history first, then point at the failing step
        lines.append("**Conversation history:**")
        lines.append("")
        for step in run.steps:
            marker = _step_marker(step.status)
            lines.append(f"- {marker} **user:** {_quote(step.prompt)}")
            if step.status == "skipped":
                lines.append(f"  - _(step skipped after earlier failure)_")
                continue
            if step.response:
                lines.append(f"  - **assistant:** {_quote(step.response)}")
        lines.append("")

    # For each failing step, show the failed assertions and the response
    failing_steps = [s for s in run.steps if s.status == "failed"]
    for step in failing_steps:
        if is_multi_step:
            lines.append(f"**Failed at step {step.step}:** `{_truncate(step.prompt, 80)}`")
        else:
            lines.append(f"**Prompt:** `{_truncate(step.prompt, 200)}`")
        lines.append("")
        if step.response:
            lines.append(f"**Response:**")
            lines.append("")
            lines.append("```")
            lines.append(_truncate(step.response, 1000))
            lines.append("```")
            lines.append("")
        lines.append("**Failed assertions:**")
        lines.append("")
        for assertion in step.assertions:
            if assertion.passed:
                continue
            lines.append(f"- {_format_assertion(assertion)}")
        lines.append("")


def _write_passed_tests(lines: list[str], report: RunReport) -> None:
    passed = [t for t in report.results if t.passed]
    if not passed:
        return
    lines.append(f"## Passed tests ({len(passed)})")
    lines.append("")
    for test in passed:
        suffix = ""
        if test.repeat > 1:
            suffix = f" (×{test.repeat}, consistency {test.consistency:.0f}%)"
        lines.append(f"- {test.name}{suffix}")
    lines.append("")


def _write_footer(lines: list[str], report: RunReport) -> None:
    if report.passed:
        return
    lines.append("---")
    lines.append("")
    lines.append("## Suggested next steps for an AI agent")
    lines.append("")
    lines.append(
        "1. Read the failure details above. For each failed test, examine the "
        "prompt, the actual response, and the assertion that did not match."
    )
    lines.append(
        "2. For multi-turn failures, look at the full conversation history to "
        "understand what context the agent had at the failing step."
    )
    lines.append(
        "3. For tests with low consistency, the failure is intermittent — "
        "check for non-determinism in the agent's behavior (sampling "
        "temperature, race conditions, missing state)."
    )
    lines.append(
        "4. Suggest a concrete change: a system prompt update, a guardrail, a "
        "schema fix, or a code change in the agent itself."
    )
    lines.append("")


# --- helpers ---


def _detect_failure_pattern(failed_runs: list[RunResult]) -> str | None:
    """Identify a common failing-step index across multi-turn failures."""
    failing_step_nums = []
    for run in failed_runs:
        for step in run.steps:
            if step.status == "failed":
                failing_step_nums.append(step.step)
                break
    if not failing_step_nums:
        return None
    counter = Counter(failing_step_nums)
    most_common_step, count = counter.most_common(1)[0]
    if count == len(failing_step_nums):
        return f"all {count} failures occur at step {most_common_step}"
    if count >= len(failing_step_nums) * 0.66:
        return f"{count}/{len(failing_step_nums)} failures occur at step {most_common_step}"
    return None


def _format_assertion(assertion: AssertionResult) -> str:
    label = f"`{assertion.type}`"
    if assertion.mode:
        label = f"`{assertion.type}({assertion.mode})`"
    expected_str = ""
    if assertion.expected is not None:
        expected_str = f" (expected: `{assertion.expected}`)"
    return f"{label}{expected_str} — {assertion.detail}"


def _step_marker(status: str) -> str:
    return {"passed": "✓", "failed": "✗", "skipped": "⊘"}.get(status, "?")


def _quote(text: str) -> str:
    """Inline-safe single-line quote for markdown bullet lists."""
    return text.replace("\n", " ").strip()


def _truncate(text: str, length: int) -> str:
    text = text.replace("\r\n", "\n")
    return text if len(text) <= length else text[: length - 1] + "…"
