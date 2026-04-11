from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from agent_shield.config.schema import (
    AssertionResult,
    RunReport,
    RunResult,
    StepResult,
    TestResult,
)
from agent_shield.reporter.html_report import write_html_report
from agent_shield.reporter.junit_report import write_junit_report
from agent_shield.reporter.markdown_report import write_markdown_report


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
    prompt: str | None = None,
) -> StepResult:
    return StepResult(
        step=step,
        prompt=prompt or f"prompt {step}",
        response=response,
        status=status,
        score=score,
        assertions=[make_assertion(passed=status == "passed") for _ in range(n_assertions)],
    )


def make_run(run: int = 1, passed: bool = True, n_steps: int = 1) -> RunResult:
    steps = [
        make_step(step=i + 1, status="passed" if passed else "failed")
        for i in range(n_steps)
    ]
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


# ============================================================
# JUnit XML reporter
# ============================================================


class TestJunitReport:
    def test_writes_valid_xml(self, tmp_path: Path):
        report = make_report([make_test()])
        out = tmp_path / "junit.xml"

        write_junit_report(report, str(out))

        # Should parse as valid XML
        tree = ET.parse(out)
        root = tree.getroot()
        assert root.tag == "testsuites"
        assert root.attrib["tests"] == "1"
        assert root.attrib["failures"] == "0"

    def test_passing_test_has_no_failure_element(self, tmp_path: Path):
        report = make_report([make_test(name="Pass")])
        out = tmp_path / "junit.xml"

        write_junit_report(report, str(out))

        root = ET.parse(out).getroot()
        case = root.find("testsuite/testcase")
        assert case is not None
        assert case.attrib["name"] == "Pass"
        assert case.find("failure") is None

    def test_failing_test_has_failure_element_with_detail(self, tmp_path: Path):
        run = RunResult(
            run=1,
            passed=False,
            score=50.0,
            duration_ms=100,
            steps=[
                StepResult(
                    step=1,
                    prompt="test prompt",
                    response="bad response",
                    status="failed",
                    score=50.0,
                    assertions=[
                        make_assertion(passed=False, type="not-contains", detail="Found forbidden: ['null']"),
                    ],
                )
            ],
        )
        test = make_test(name="Fail", runs=[run], passed=False)
        out = tmp_path / "junit.xml"

        write_junit_report(make_report([test]), str(out))

        root = ET.parse(out).getroot()
        failure = root.find("testsuite/testcase/failure")
        assert failure is not None
        assert "score" in failure.attrib["message"]
        assert "not-contains" in failure.text
        assert "Found forbidden" in failure.text
        assert "bad response" in failure.text

    def test_repeat_failure_shows_pass_rate(self, tmp_path: Path):
        runs = [make_run(run=1, passed=True), make_run(run=2, passed=False), make_run(run=3, passed=False)]
        test = TestResult(
            name="Flaky",
            type="single-turn",
            repeat=3,
            pass_rate=33.0,
            consistency=66.0,
            score=33.0,
            passed=False,
            runs=runs,
            duration_ms=300,
        )
        out = tmp_path / "junit.xml"

        write_junit_report(make_report([test]), str(out))

        failure = ET.parse(out).getroot().find("testsuite/testcase/failure")
        assert failure is not None
        assert "33% pass rate" in failure.attrib["message"]
        assert "1/3 runs" in failure.attrib["message"]
        assert "Sample failure" in failure.text

    def test_multi_turn_failure_shows_step(self, tmp_path: Path):
        run = RunResult(
            run=1,
            passed=False,
            score=33.3,
            duration_ms=200,
            steps=[
                make_step(step=1, status="passed", prompt="step one"),
                make_step(step=2, status="failed", prompt="step two", response="bad"),
                StepResult(
                    step=3, prompt="step three", response="", status="skipped", score=0, assertions=[]
                ),
            ],
        )
        test = make_test(name="MT", type_="multi-turn", runs=[run], passed=False)
        out = tmp_path / "junit.xml"

        write_junit_report(make_report([test]), str(out))

        failure = ET.parse(out).getroot().find("testsuite/testcase/failure")
        assert failure is not None
        assert "Step 1" in failure.text
        assert "Step 2" in failure.text
        assert "step two" in failure.text
        assert "skipped" in failure.text.lower()

    def test_creates_parent_directories(self, tmp_path: Path):
        out = tmp_path / "nested" / "deep" / "junit.xml"
        write_junit_report(make_report([make_test()]), str(out))
        assert out.exists()


# ============================================================
# Markdown reporter
# ============================================================


class TestMarkdownReport:
    def test_writes_basic_report(self, tmp_path: Path):
        out = tmp_path / "report.md"
        write_markdown_report(make_report([make_test()]), str(out))
        text = out.read_text(encoding="utf-8")
        assert "# Agent Shield Report" in text
        assert "Endpoint" in text
        assert "https://agent.test/api" in text
        assert "PASSED" in text

    def test_failed_test_includes_response_and_assertion(self, tmp_path: Path):
        run = RunResult(
            run=1,
            passed=False,
            score=50.0,
            duration_ms=100,
            steps=[
                StepResult(
                    step=1,
                    prompt="say hello",
                    response="goodbye there",
                    status="failed",
                    score=50.0,
                    assertions=[
                        make_assertion(passed=False, type="contains", detail="Missing: ['hello']"),
                    ],
                )
            ],
        )
        test = make_test(name="Greeting", runs=[run], passed=False)
        out = tmp_path / "report.md"

        write_markdown_report(make_report([test]), str(out))

        text = out.read_text(encoding="utf-8")
        assert "## Failed tests" in text
        assert "### Greeting" in text
        assert "say hello" in text
        assert "goodbye there" in text
        assert "Missing: ['hello']" in text

    def test_passed_tests_listed_at_bottom(self, tmp_path: Path):
        report = make_report([make_test(name="Pass A"), make_test(name="Pass B")])
        out = tmp_path / "report.md"

        write_markdown_report(report, str(out))

        text = out.read_text(encoding="utf-8")
        assert "## Passed tests (2)" in text
        assert "- Pass A" in text
        assert "- Pass B" in text

    def test_multi_turn_shows_full_conversation(self, tmp_path: Path):
        run = RunResult(
            run=1,
            passed=False,
            score=33.3,
            duration_ms=300,
            steps=[
                make_step(step=1, status="passed", prompt="book a workout", response="what type?"),
                make_step(step=2, status="failed", prompt="strength", response="great!"),
                StepResult(step=3, prompt="tomorrow 10am", response="", status="skipped", score=0, assertions=[]),
            ],
        )
        test = make_test(name="Booking", type_="multi-turn", runs=[run], passed=False)
        out = tmp_path / "report.md"

        write_markdown_report(make_report([test]), str(out))

        text = out.read_text(encoding="utf-8")
        assert "Conversation history" in text
        assert "book a workout" in text
        assert "what type?" in text
        assert "strength" in text
        assert "step skipped" in text

    def test_repeat_pattern_detection(self, tmp_path: Path):
        # All 3 failures happen at step 2 → pattern should be detected
        runs = []
        for i in range(3):
            runs.append(
                RunResult(
                    run=i + 1,
                    passed=False,
                    score=33.3,
                    duration_ms=200,
                    steps=[
                        make_step(step=1, status="passed"),
                        make_step(step=2, status="failed"),
                        StepResult(step=3, prompt="p3", response="", status="skipped", score=0, assertions=[]),
                    ],
                )
            )
        test = TestResult(
            name="Pattern",
            type="multi-turn",
            repeat=3,
            pass_rate=0.0,
            consistency=100.0,
            score=0.0,
            passed=False,
            runs=runs,
            duration_ms=600,
        )
        out = tmp_path / "report.md"

        write_markdown_report(make_report([test]), str(out))

        text = out.read_text(encoding="utf-8")
        assert "Failure pattern" in text
        assert "step 2" in text

    def test_passed_report_omits_next_steps_section(self, tmp_path: Path):
        out = tmp_path / "report.md"
        write_markdown_report(make_report([make_test()]), str(out))
        text = out.read_text(encoding="utf-8")
        assert "Suggested next steps" not in text

    def test_failed_report_includes_next_steps_section(self, tmp_path: Path):
        out = tmp_path / "report.md"
        write_markdown_report(make_report([make_test(passed=False)]), str(out))
        text = out.read_text(encoding="utf-8")
        assert "Suggested next steps" in text


# ============================================================
# HTML reporter
# ============================================================


class TestHtmlReport:
    def test_writes_valid_html_document(self, tmp_path: Path):
        out = tmp_path / "report.html"
        write_html_report(make_report([make_test()]), str(out))
        text = out.read_text(encoding="utf-8")

        assert text.startswith("<!DOCTYPE html>")
        assert "<title>" in text
        assert "</html>" in text
        assert "Agent Shield Report" in text

    def test_self_contained_no_external_assets(self, tmp_path: Path):
        out = tmp_path / "report.html"
        write_html_report(make_report([make_test()]), str(out))
        text = out.read_text(encoding="utf-8")

        # CSS embedded in <style>, JS embedded in <script> — no external refs
        assert "<style>" in text
        assert "<script>" in text
        assert 'src="http' not in text
        assert 'href="http' not in text

    def test_renders_pre_filled_test_data(self, tmp_path: Path):
        # Hybrid: test data should be in the HTML, not lazily loaded
        run = RunResult(
            run=1,
            passed=False,
            score=50,
            duration_ms=100,
            steps=[
                StepResult(
                    step=1,
                    prompt="distinctive prompt text",
                    response="distinctive response text",
                    status="failed",
                    score=50,
                    assertions=[make_assertion(passed=False, detail="distinct detail")],
                )
            ],
        )
        test = make_test(name="DistinctTestName", runs=[run], passed=False)
        out = tmp_path / "report.html"

        write_html_report(make_report([test]), str(out))

        text = out.read_text(encoding="utf-8")
        # Pre-rendered: all of these should appear in raw HTML
        assert "DistinctTestName" in text
        assert "distinctive prompt text" in text
        assert "distinctive response text" in text
        assert "distinct detail" in text

    def test_html_escapes_special_chars(self, tmp_path: Path):
        run = RunResult(
            run=1,
            passed=False,
            score=0,
            duration_ms=100,
            steps=[
                StepResult(
                    step=1,
                    prompt="<script>alert('xss')</script>",
                    response="<b>not bold</b>",
                    status="failed",
                    score=0,
                    assertions=[make_assertion(passed=False, detail="<bad>")],
                )
            ],
        )
        test = make_test(name="<XSS>", runs=[run], passed=False)
        out = tmp_path / "report.html"

        write_html_report(make_report([test]), str(out))

        text = out.read_text(encoding="utf-8")
        # Raw script tag from user data must NOT appear unescaped
        assert "<script>alert('xss')</script>" not in text
        assert "&lt;script&gt;" in text
        assert "&lt;b&gt;not bold&lt;/b&gt;" in text
        assert "&lt;XSS&gt;" in text

    def test_default_excludes_passing_runs_from_repeated_tests(self, tmp_path: Path):
        # 3 passing + 1 failing → only the 1 failing should appear, plus a note
        runs = [
            make_run(run=1, passed=True),
            make_run(run=2, passed=True),
            make_run(run=3, passed=True),
            make_run(run=4, passed=False),
        ]
        test = TestResult(
            name="Mostly passing",
            type="single-turn",
            repeat=4,
            pass_rate=75.0,
            consistency=75.0,
            score=75.0,
            passed=False,
            runs=runs,
            duration_ms=400,
        )
        out = tmp_path / "report.html"

        write_html_report(make_report([test]), str(out))

        text = out.read_text(encoding="utf-8")
        # The note about hidden runs should appear
        assert "3 passing runs hidden" in text
        # Only one .run element should be present
        assert text.count('class="run pass"') == 0
        assert text.count('data-status="passed"') == 0
        assert text.count('data-status="failed"') == 1

    def test_include_passing_runs_keeps_everything(self, tmp_path: Path):
        runs = [
            make_run(run=1, passed=True),
            make_run(run=2, passed=False),
        ]
        test = TestResult(
            name="Mixed",
            type="single-turn",
            repeat=2,
            pass_rate=50,
            consistency=50,
            score=50,
            passed=False,
            runs=runs,
            duration_ms=200,
        )
        out = tmp_path / "report.html"

        write_html_report(make_report([test]), str(out), include_passing_runs=True)

        text = out.read_text(encoding="utf-8")
        assert "hidden by default" not in text
        assert text.count('data-status="passed"') == 1
        assert text.count('data-status="failed"') == 1

    def test_filter_buttons_present_for_repeated_tests(self, tmp_path: Path):
        runs = [make_run(run=i + 1, passed=(i % 2 == 0)) for i in range(4)]
        test = TestResult(
            name="Filterable",
            type="single-turn",
            repeat=4,
            pass_rate=50,
            consistency=50,
            score=50,
            passed=False,
            runs=runs,
            duration_ms=400,
        )
        out = tmp_path / "report.html"

        write_html_report(make_report([test]), str(out), include_passing_runs=True)

        text = out.read_text(encoding="utf-8")
        assert 'data-filter="all"' in text
        assert 'data-filter="failed"' in text
        assert 'data-filter="passed"' in text

    def test_no_filter_bar_for_single_run_tests(self, tmp_path: Path):
        out = tmp_path / "report.html"
        write_html_report(make_report([make_test()]), str(out))
        text = out.read_text(encoding="utf-8")
        assert 'data-filter=' not in text

    def test_creates_parent_directories(self, tmp_path: Path):
        out = tmp_path / "nested" / "deep" / "report.html"
        write_html_report(make_report([make_test()]), str(out))
        assert out.exists()

    def test_overall_passed_status_in_summary(self, tmp_path: Path):
        out = tmp_path / "report.html"
        write_html_report(make_report([make_test()], threshold=85), str(out))
        text = out.read_text(encoding="utf-8")
        assert ">PASSED<" in text
        assert "overall pass" in text

    def test_overall_failed_status_in_summary(self, tmp_path: Path):
        out = tmp_path / "report.html"
        write_html_report(make_report([make_test(passed=False)], threshold=85), str(out))
        text = out.read_text(encoding="utf-8")
        assert ">FAILED<" in text
        assert "overall fail" in text
