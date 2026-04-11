from __future__ import annotations

import json
from pathlib import Path

import pytest
from rich.console import Console

from agent_shield.config.schema import (
    AssertionResult,
    RunReport,
    RunResult,
    StepResult,
    TestResult,
)
from agent_shield.reporter.json_report import write_json_report
from agent_shield.reporter.terminal import print_results


def make_assertion(passed: bool = True, **overrides) -> AssertionResult:
    base = {
        "type": "contains",
        "expected": ["hello"],
        "passed": passed,
        "detail": "All values found in response" if passed else "Missing: ['hello']",
    }
    base.update(overrides)
    return AssertionResult(**base)


def make_step(
    step: int = 1,
    status: str = "passed",
    score: float = 100.0,
    n_assertions: int = 1,
    response: str = "hello world",
) -> StepResult:
    return StepResult(
        step=step,
        prompt=f"prompt {step}",
        response=response,
        status=status,
        score=score,
        assertions=[make_assertion(passed=status == "passed") for _ in range(n_assertions)],
    )


def make_run(
    run: int = 1,
    passed: bool = True,
    n_steps: int = 1,
) -> RunResult:
    steps = [make_step(step=i + 1, status="passed" if passed else "failed") for i in range(n_steps)]
    return RunResult(
        run=run,
        passed=passed,
        score=100.0 if passed else 0.0,
        duration_ms=100,
        steps=steps,
    )


def make_test(
    name: str = "Test 1",
    type_: str = "single-turn",
    repeat: int = 1,
    runs: list[RunResult] | None = None,
    passed: bool = True,
) -> TestResult:
    if runs is None:
        runs = [make_run(run=1, passed=passed)]
    return TestResult(
        name=name,
        type=type_,
        repeat=repeat,
        pass_rate=100.0 if passed else 0.0,
        consistency=100.0,
        score=100.0 if passed else 0.0,
        passed=passed,
        runs=runs,
        duration_ms=sum(r.duration_ms for r in runs),
    )


def make_report(tests: list[TestResult], threshold: int = 85) -> RunReport:
    overall = sum(t.score for t in tests) / len(tests) if tests else 0.0
    passed = sum(1 for t in tests if t.passed)
    return RunReport(
        run_id="test-uuid",
        timestamp="2026-04-11T12:00:00Z",
        agent_endpoint="https://agent.test/api",
        overall_score=overall,
        threshold=threshold,
        passed=overall >= threshold,
        total_tests=len(tests),
        passed_tests=passed,
        failed_tests=len(tests) - passed,
        duration_ms=sum(t.duration_ms for t in tests),
        results=tests,
    )


# --- JSON reporter ---


class TestJsonReport:
    def test_writes_full_report(self, tmp_path: Path):
        report = make_report([make_test()])
        out = tmp_path / "report.json"

        result_path = write_json_report(report, str(out))

        assert result_path == out
        assert out.exists()
        data = json.loads(out.read_text())
        assert data["run_id"] == "test-uuid"
        assert data["overall_score"] == 100.0
        assert len(data["results"]) == 1
        assert data["results"][0]["name"] == "Test 1"

    def test_creates_parent_directories(self, tmp_path: Path):
        out = tmp_path / "nested" / "deeply" / "report.json"
        report = make_report([make_test()])

        write_json_report(report, str(out))

        assert out.exists()

    def test_includes_all_runs(self, tmp_path: Path):
        # 3 repeat runs — all should appear in JSON, not just a sample
        runs = [make_run(run=i + 1, passed=(i == 0)) for i in range(3)]
        test = make_test(repeat=3, runs=runs, passed=False)
        report = make_report([test])
        out = tmp_path / "report.json"

        write_json_report(report, str(out))

        data = json.loads(out.read_text())
        assert len(data["results"][0]["runs"]) == 3

    def test_serializes_multi_turn_steps(self, tmp_path: Path):
        run = RunResult(
            run=1,
            passed=False,
            score=33.3,
            duration_ms=200,
            steps=[
                make_step(step=1, status="passed"),
                make_step(step=2, status="failed", score=0),
                make_step(step=3, status="skipped", score=0, n_assertions=0),
            ],
        )
        test = make_test(name="multi", type_="multi-turn", runs=[run], passed=False)
        report = make_report([test])
        out = tmp_path / "report.json"

        write_json_report(report, str(out))

        data = json.loads(out.read_text())
        steps = data["results"][0]["runs"][0]["steps"]
        assert [s["status"] for s in steps] == ["passed", "failed", "skipped"]


# --- Terminal reporter ---


def _capture(report: RunReport, verbose: bool = False) -> str:
    """Render the terminal report into a string for assertions."""
    console = Console(record=True, width=120)
    # Patch the module-level function to use our recording console
    from agent_shield.reporter import terminal as term

    # Inject our console by calling the helper functions directly
    term.print_header(console, "0.1.0", report.total_tests, report.agent_endpoint)
    for test in report.results:
        term._print_test(console, test, verbose)
    console.print()
    term._print_summary(console, report)

    return console.export_text()


class TestTerminalReporter:
    def test_passing_single_turn(self):
        report = make_report([make_test(name="Returns workout plan")])
        output = _capture(report)

        assert "Agent Shield v0.1.0" in output
        assert "Returns workout plan" in output
        assert "100.0%" in output
        assert "PASSED" in output
        assert "https://agent.test/api" in output

    def test_failing_assertion_shown(self):
        run = RunResult(
            run=1,
            passed=False,
            score=50.0,
            duration_ms=100,
            steps=[
                StepResult(
                    step=1,
                    prompt="prompt",
                    response="bad response",
                    status="failed",
                    score=50.0,
                    assertions=[
                        make_assertion(passed=True),
                        make_assertion(
                            passed=False, type="not-contains", detail="Found forbidden: ['null']"
                        ),
                    ],
                )
            ],
        )
        test = make_test(name="Handles gibberish", runs=[run], passed=False)
        output = _capture(make_report([test]))

        # Failing assertion detail should be visible by default
        assert "not-contains" in output
        assert "Found forbidden" in output
        # Passing assertion detail should NOT be visible without verbose
        assert "All values found" not in output

    def test_verbose_shows_passing_assertions_and_response(self):
        report = make_report([make_test()])
        output = _capture(report, verbose=True)

        assert "All values found" in output
        assert "response:" in output

    def test_multi_turn_step_glyphs(self):
        run = RunResult(
            run=1,
            passed=False,
            score=33.3,
            duration_ms=100,
            steps=[
                make_step(step=1, status="passed"),
                make_step(step=2, status="failed"),
                StepResult(
                    step=3,
                    prompt="step3",
                    response="",
                    status="skipped",
                    score=0,
                    assertions=[],
                ),
            ],
        )
        test = make_test(name="Multi flow", type_="multi-turn", runs=[run], passed=False)
        output = _capture(make_report([test]))

        # Step listing should show all 3 steps
        assert "step 1" in output
        assert "step 2" in output
        assert "step 3" in output

    def test_repeat_shows_consistency_and_sample_failure(self):
        runs = [
            make_run(run=1, passed=True),
            make_run(run=2, passed=False),
            make_run(run=3, passed=True),
            make_run(run=4, passed=False),
            make_run(run=5, passed=True),
        ]
        test = TestResult(
            name="Flaky test",
            type="single-turn",
            repeat=5,
            pass_rate=60.0,
            consistency=60.0,
            score=60.0,
            passed=False,
            runs=runs,
            duration_ms=500,
        )
        output = _capture(make_report([test]))

        assert "x5" in output or "\u00d75" in output
        assert "consistency: 60%" in output
        assert "3/5 runs passed" in output
        assert "2/5 failed" in output
        assert "sample failure" in output

    def test_failing_overall_summary(self):
        report = make_report([make_test(passed=False)], threshold=85)
        output = _capture(report)

        assert "FAILED" in output
        assert "0.0%" in output or "0%" in output
        assert "threshold: 85%" in output

    def test_print_results_smoke(self, capsys):
        # Smoke test the public entry point — no exceptions, output is non-empty
        report = make_report([make_test()])
        print_results(report, verbose=False, version="0.1.0")
        captured = capsys.readouterr()
        assert "Agent Shield" in captured.out
        assert "PASSED" in captured.out
