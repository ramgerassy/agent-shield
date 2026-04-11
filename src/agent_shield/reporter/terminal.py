from __future__ import annotations

import io
import sys

from rich.console import Console

from agent_shield.config.schema import RunReport, RunResult, StepResult, TestResult


# Cached UTF-8 stream — created once per process. We must not let this be
# garbage collected, because TextIOWrapper closes the underlying buffer on
# destruction, which would break sys.stdout for any later code in the process
# (including subsequent tests in a test session).
_utf8_stream: io.TextIOWrapper | None = None


def _make_console() -> Console:
    """Build a Console that always writes UTF-8.

    Some environments (Windows consoles with legacy codepages, minimal
    Linux containers with LANG=C, etc.) report stdout's encoding as
    something other than UTF-8 and crash on the Unicode glyphs we use
    (✓ ✗ ⊘ × …). We wrap `sys.stdout.buffer` in a UTF-8 TextIOWrapper
    with `errors='replace'` so unsupported terminals degrade to '?'
    instead of raising UnicodeEncodeError.

    `TextIOWrapper.isatty()` delegates to the underlying buffer, so
    Rich's TTY detection (and color decisions) still work correctly.
    If `sys.stdout` is already a wrapper around `sys.stdout.buffer`
    that we can't access (rare — e.g., captured stdout in some test
    harnesses), fall back to the default Console.

    The wrapper is cached at module level so it is never garbage
    collected — TextIOWrapper closes the underlying buffer on
    destruction, which would corrupt sys.stdout for the rest of the
    process.
    """
    global _utf8_stream
    if _utf8_stream is None:
        try:
            _utf8_stream = io.TextIOWrapper(
                sys.stdout.buffer,
                encoding="utf-8",
                errors="replace",
                line_buffering=True,
                write_through=True,
            )
        except (AttributeError, io.UnsupportedOperation):
            return Console()
    return Console(file=_utf8_stream)


# Status indicators
PASS_MARK = "[green]\u2713[/green]"  # ✓
FAIL_MARK = "[red]\u2717[/red]"  # ✗
SKIP_MARK = "[yellow]\u2298[/yellow]"  # ⊘


def print_header(console: Console, version: str, num_tests: int, endpoint: str) -> None:
    console.print()
    console.print(
        f" [bold]Agent Shield v{version}[/bold] \u2014 "
        f"Running {num_tests} tests against [cyan]{endpoint}[/cyan]"
    )
    console.print()


def print_results(
    report: RunReport,
    verbose: bool = False,
    version: str = "0.1.0",
) -> None:
    """Print the full run report to stdout using Rich."""
    console = _make_console()
    print_header(console, version, report.total_tests, report.agent_endpoint)

    for test in report.results:
        _print_test(console, test, verbose)

    console.print()
    _print_summary(console, report)


def _print_test(console: Console, test: TestResult, verbose: bool) -> None:
    mark = PASS_MARK if test.passed else FAIL_MARK
    name_col = test.name.ljust(45)[:45]
    score_str = f"{test.score:5.1f}%"

    if test.repeat > 1:
        # Repeated test → show ×N and consistency
        line = (
            f" {mark} {name_col}  [bold]{score_str}[/bold]  "
            f"\u00d7{test.repeat} consistency: {test.consistency:.0f}%"
        )
    elif test.type == "multi-turn":
        # Single-run multi-turn → show step glyphs inline
        glyphs = " ".join(_step_glyph(s) for s in test.runs[0].steps)
        line = f" {mark} {name_col}  [bold]{score_str}[/bold]  (steps: {glyphs})"
    else:
        # Single-turn, single run
        run = test.runs[0] if test.runs else None
        if run and run.steps:
            n = len(run.steps[0].assertions)
            passed = sum(1 for a in run.steps[0].assertions if a.passed)
            line = (
                f" {mark} {name_col}  [bold]{score_str}[/bold]  "
                f"({passed}/{n} assertions)"
            )
        else:
            line = f" {mark} {name_col}  [bold]{score_str}[/bold]"

    console.print(line)

    # Detail expansion: show failures (always) and full content (verbose)
    if test.repeat > 1:
        _print_repeat_details(console, test, verbose)
    else:
        _print_run_details(console, test.runs[0], verbose)


def _print_repeat_details(console: Console, test: TestResult, verbose: bool) -> None:
    passed_count = sum(1 for r in test.runs if r.passed)
    failed_count = test.repeat - passed_count

    console.print(
        f"   {passed_count}/{test.repeat} runs passed, "
        f"{failed_count}/{test.repeat} failed"
    )

    if verbose:
        for run in test.runs:
            console.print(f"   run {run.run}: {'PASS' if run.passed else 'FAIL'}")
            _print_run_details(console, run, verbose, indent="     ")
    else:
        # Show first failing run as a sample
        sample = next((r for r in test.runs if not r.passed), None)
        if sample is not None:
            console.print(f"   sample failure (run {sample.run}):")
            _print_run_details(console, sample, verbose=False, indent="     ")


def _print_run_details(
    console: Console,
    run: RunResult,
    verbose: bool,
    indent: str = "   ",
) -> None:
    """Print the per-step / per-assertion details for a single run."""
    is_multi_step = len(run.steps) > 1

    for step in run.steps:
        if is_multi_step:
            glyph = _step_glyph(step)
            console.print(
                f'{indent}step {step.step} {glyph}  "{_truncate(step.prompt, 60)}"'
            )
            if step.status == "skipped":
                continue
            assertion_indent = indent + "  "
        else:
            assertion_indent = indent

        # Always show failed assertions; show passed ones only in verbose mode
        for assertion in step.assertions:
            if assertion.passed and not verbose:
                continue
            glyph = PASS_MARK if assertion.passed else FAIL_MARK
            label = assertion.type
            if assertion.mode:
                label = f"{label}({assertion.mode})"
            console.print(f"{assertion_indent}{glyph} {label}: {assertion.detail}")

        if verbose and step.response:
            console.print(
                f"{assertion_indent}[dim]response: {_truncate(step.response, 200)}[/dim]"
            )


def _print_summary(console: Console, report: RunReport) -> None:
    status = "[green bold]PASSED[/green bold]" if report.passed else "[red bold]FAILED[/red bold]"
    console.print(
        f" Overall: [bold]{report.overall_score:.1f}%[/bold] "
        f"(threshold: {report.threshold}%) \u2014 {status}"
    )
    console.print()


def _step_glyph(step: StepResult) -> str:
    if step.status == "passed":
        return PASS_MARK
    if step.status == "failed":
        return FAIL_MARK
    return SKIP_MARK


def _truncate(text: str, length: int) -> str:
    text = text.replace("\n", " ")
    return text if len(text) <= length else text[: length - 1] + "\u2026"
