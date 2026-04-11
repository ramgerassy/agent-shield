from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from agent_shield import __version__
from agent_shield.cli import app

runner = CliRunner()


# --- helpers ---


PASSING_CONFIG = """\
agent:
  endpoint: "https://agent.test/api/chat"
  body_template:
    messages: "{{messages}}"

settings:
  threshold: 85
  timeout_ms: 5000
  concurrency: 1
  retries: 0
  output: "{output}"

tests:
  - name: "Says hello"
    prompt: "say hi"
    assert:
      - type: contains
        value: ["hello"]
"""


FAILING_CONFIG = """\
agent:
  endpoint: "https://agent.test/api/chat"
  body_template:
    messages: "{{messages}}"

settings:
  threshold: 85
  timeout_ms: 5000
  concurrency: 1
  retries: 0
  output: "{output}"

tests:
  - name: "Expects unobtainable"
    prompt: "say hi"
    assert:
      - type: contains
        value: ["nope-not-in-response"]
"""


def write_config(tmp_path: Path, content_template: str, output_name: str = "report.json") -> tuple[Path, Path]:
    output_path = tmp_path / output_name
    config_text = content_template.format(output=str(output_path).replace("\\", "/"))
    config_path = tmp_path / "agent-shield.yaml"
    config_path.write_text(config_text)
    return config_path, output_path


def mock_agent_response(text: str = "hello world") -> None:
    respx.post("https://agent.test/api/chat").mock(
        return_value=httpx.Response(200, text=text)
    )


# --- version ---


class TestVersionCommand:
    def test_prints_version(self):
        result = runner.invoke(app, ["version"])
        assert result.exit_code == 0
        assert __version__ in result.stdout
        assert "agent-shield" in result.stdout


# --- init ---


class TestInitCommand:
    def test_creates_template(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0
        target = tmp_path / "agent-shield.yaml"
        assert target.exists()
        # Template should contain key markers
        text = target.read_text(encoding="utf-8")
        assert "agent:" in text
        assert "tests:" in text
        assert "endpoint:" in text
        assert "{{messages}}" in text

    def test_refuses_overwrite_without_confirmation(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "agent-shield.yaml"
        target.write_text("existing: true\n")

        # Decline the prompt
        result = runner.invoke(app, ["init"], input="n\n")
        assert result.exit_code == 1
        assert target.read_text() == "existing: true\n"

    def test_overwrites_with_confirmation(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "agent-shield.yaml"
        target.write_text("existing: true\n")

        result = runner.invoke(app, ["init"], input="y\n")
        assert result.exit_code == 0
        assert "agent:" in target.read_text()

    def test_force_overwrites_without_prompt(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "agent-shield.yaml"
        target.write_text("existing: true\n")

        result = runner.invoke(app, ["init", "--force"])
        assert result.exit_code == 0
        assert "agent:" in target.read_text()


# --- run: errors ---


class TestRunErrors:
    def test_missing_config_exits_2(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["run"])
        assert result.exit_code == 2

    def test_explicit_missing_config_exits_2(self, tmp_path: Path):
        result = runner.invoke(app, ["run", "--config", str(tmp_path / "missing.yaml")])
        assert result.exit_code == 2

    def test_invalid_config_exits_2(self, tmp_path: Path):
        config_path = tmp_path / "agent-shield.yaml"
        config_path.write_text("not_valid_config: true\n")
        result = runner.invoke(app, ["run", "--config", str(config_path)])
        assert result.exit_code == 2

    def test_invalid_repeat_exits_2(self, tmp_path: Path):
        config_path, _ = write_config(tmp_path, PASSING_CONFIG)
        result = runner.invoke(
            app, ["run", "--config", str(config_path), "--repeat", "0"]
        )
        assert result.exit_code == 2


# --- run: success ---


class TestRunSuccess:
    @respx.mock
    def test_run_writes_json_report(self, tmp_path: Path):
        mock_agent_response("hello world")
        config_path, output_path = write_config(tmp_path, PASSING_CONFIG)

        result = runner.invoke(app, ["run", "--config", str(config_path)])

        assert result.exit_code == 0
        assert output_path.exists()
        data = json.loads(output_path.read_text())
        assert data["passed"] is True
        assert data["overall_score"] == 100.0
        assert len(data["results"]) == 1
        assert data["results"][0]["name"] == "Says hello"

    @respx.mock
    def test_run_without_ci_returns_zero_on_failure(self, tmp_path: Path):
        # Without --ci, a failed run still exits 0 (informational)
        mock_agent_response("hello world")
        config_path, _ = write_config(tmp_path, FAILING_CONFIG)

        result = runner.invoke(app, ["run", "--config", str(config_path)])

        assert result.exit_code == 0

    @respx.mock
    def test_ci_mode_passing_exits_zero(self, tmp_path: Path):
        mock_agent_response("hello world")
        config_path, _ = write_config(tmp_path, PASSING_CONFIG)

        result = runner.invoke(app, ["run", "--config", str(config_path), "--ci"])

        assert result.exit_code == 0

    @respx.mock
    def test_ci_mode_failing_exits_one(self, tmp_path: Path):
        mock_agent_response("hello world")
        config_path, _ = write_config(tmp_path, FAILING_CONFIG)

        result = runner.invoke(app, ["run", "--config", str(config_path), "--ci"])

        assert result.exit_code == 1

    @respx.mock
    def test_ci_mode_auto_writes_junit(self, tmp_path: Path, monkeypatch):
        # In --ci mode without an explicit --junit path, the default
        # ./agent-shield-junit.xml in CWD should be created
        monkeypatch.chdir(tmp_path)
        mock_agent_response("hello world")
        config_path, _ = write_config(tmp_path, PASSING_CONFIG)

        result = runner.invoke(app, ["run", "--config", str(config_path), "--ci"])

        assert result.exit_code == 0
        junit_default = tmp_path / "agent-shield-junit.xml"
        assert junit_default.exists()
        text = junit_default.read_text()
        assert "<testsuites" in text

    @respx.mock
    def test_explicit_junit_path(self, tmp_path: Path):
        mock_agent_response("hello world")
        config_path, _ = write_config(tmp_path, PASSING_CONFIG)
        junit_path = tmp_path / "custom-junit.xml"

        result = runner.invoke(
            app,
            ["run", "--config", str(config_path), "--junit", str(junit_path)],
        )

        assert result.exit_code == 0
        assert junit_path.exists()

    @respx.mock
    def test_html_path_creates_html(self, tmp_path: Path):
        mock_agent_response("hello world")
        config_path, _ = write_config(tmp_path, PASSING_CONFIG)
        html_path = tmp_path / "report.html"

        result = runner.invoke(
            app,
            ["run", "--config", str(config_path), "--html", str(html_path)],
        )

        assert result.exit_code == 0
        assert html_path.exists()
        assert "<!DOCTYPE html>" in html_path.read_text(encoding="utf-8")

    @respx.mock
    def test_markdown_path_creates_markdown(self, tmp_path: Path):
        mock_agent_response("hello world")
        config_path, _ = write_config(tmp_path, PASSING_CONFIG)
        md_path = tmp_path / "report.md"

        result = runner.invoke(
            app,
            ["run", "--config", str(config_path), "--markdown", str(md_path)],
        )

        assert result.exit_code == 0
        assert md_path.exists()
        text = md_path.read_text(encoding="utf-8")
        assert "# Agent Shield Report" in text

    @respx.mock
    def test_repeat_override(self, tmp_path: Path):
        mock_agent_response("hello world")
        config_path, output_path = write_config(tmp_path, PASSING_CONFIG)

        result = runner.invoke(
            app,
            ["run", "--config", str(config_path), "--repeat", "5"],
        )

        assert result.exit_code == 0
        data = json.loads(output_path.read_text())
        assert data["results"][0]["repeat"] == 5
        assert len(data["results"][0]["runs"]) == 5

    @respx.mock
    def test_no_config_flag_uses_cwd_discovery(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mock_agent_response("hello world")
        write_config(tmp_path, PASSING_CONFIG)

        result = runner.invoke(app, ["run"])

        assert result.exit_code == 0

    @respx.mock
    def test_all_outputs_at_once(self, tmp_path: Path):
        mock_agent_response("hello world")
        config_path, json_path = write_config(tmp_path, PASSING_CONFIG)
        junit = tmp_path / "junit.xml"
        html = tmp_path / "report.html"
        md = tmp_path / "report.md"

        result = runner.invoke(
            app,
            [
                "run",
                "--config", str(config_path),
                "--junit", str(junit),
                "--html", str(html),
                "--markdown", str(md),
            ],
        )

        assert result.exit_code == 0
        assert json_path.exists()
        assert junit.exists()
        assert html.exists()
        assert md.exists()
