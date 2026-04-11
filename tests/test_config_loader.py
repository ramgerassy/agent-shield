from pathlib import Path

import pytest

from agent_shield.config.loader import load_config, resolve_config_path

VALID_YAML = """\
agent:
  endpoint: "https://example.com/api/chat"
  headers:
    Authorization: "Bearer test-key"
  body_template:
    message: "{{prompt}}"

settings:
  threshold: 90
  timeout_ms: 5000

tests:
  - name: "Basic test"
    prompt: "Hello"
    assert:
      - type: contains
        value: ["hello"]
"""

YAML_WITH_ENV = """\
agent:
  endpoint: "https://example.com/api/chat"
  headers:
    Authorization: "Bearer {{TEST_API_KEY}}"

tests:
  - name: "Test"
    prompt: "Hello"
    assert:
      - type: contains
        value: ["hello"]
"""

INVALID_YAML = """\
agent:
  endpoint: "https://example.com/api/chat"
tests: "not a list"
"""


class TestLoadConfig:
    def test_load_valid_yaml(self, tmp_path: Path):
        config_file = tmp_path / "agent-shield.yaml"
        config_file.write_text(VALID_YAML)

        config = load_config(config_file)

        assert config.agent.endpoint == "https://example.com/api/chat"
        assert config.settings.threshold == 90
        assert config.settings.timeout_ms == 5000
        assert len(config.tests) == 1
        assert config.tests[0].name == "Basic test"
        assert len(config.tests[0].assertions) == 1

    def test_load_yml_extension(self, tmp_path: Path):
        config_file = tmp_path / "config.yml"
        config_file.write_text(VALID_YAML)

        config = load_config(config_file)
        assert config.agent.endpoint == "https://example.com/api/chat"

    def test_env_var_resolution(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("TEST_API_KEY", "secret-123")
        config_file = tmp_path / "agent-shield.yaml"
        config_file.write_text(YAML_WITH_ENV)

        config = load_config(config_file)
        assert config.agent.headers["Authorization"] == "Bearer secret-123"

    def test_missing_env_var_raises(self, tmp_path: Path, monkeypatch):
        monkeypatch.delenv("TEST_API_KEY", raising=False)
        config_file = tmp_path / "agent-shield.yaml"
        config_file.write_text(YAML_WITH_ENV)

        with pytest.raises(ValueError, match="Environment variable 'TEST_API_KEY' is not set"):
            load_config(config_file)

    def test_prompt_placeholder_not_resolved(self, tmp_path: Path):
        config_file = tmp_path / "agent-shield.yaml"
        config_file.write_text(VALID_YAML)

        config = load_config(config_file)
        assert config.agent.body_template["message"] == "{{prompt}}"

    def test_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_config(tmp_path / "nonexistent.yaml")

    def test_invalid_yaml_structure_raises(self, tmp_path: Path):
        config_file = tmp_path / "agent-shield.yaml"
        config_file.write_text(INVALID_YAML)

        with pytest.raises(ValueError, match="Invalid config"):
            load_config(config_file)

    def test_non_mapping_yaml_raises(self, tmp_path: Path):
        config_file = tmp_path / "agent-shield.yaml"
        config_file.write_text("- just\n- a\n- list\n")

        with pytest.raises(ValueError, match="must contain a YAML mapping"):
            load_config(config_file)


class TestResolveConfigPath:
    def test_explicit_path(self, tmp_path: Path):
        config_file = tmp_path / "custom.yaml"
        config_file.write_text(VALID_YAML)

        result = resolve_config_path(str(config_file))
        assert result == config_file

    def test_explicit_path_not_found(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            resolve_config_path(str(tmp_path / "missing.yaml"))

    def test_auto_discover_yaml(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config_file = tmp_path / "agent-shield.yaml"
        config_file.write_text(VALID_YAML)

        result = resolve_config_path()
        assert result == config_file

    def test_auto_discover_yml(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config_file = tmp_path / "agent-shield.yml"
        config_file.write_text(VALID_YAML)

        result = resolve_config_path()
        assert result == config_file

    def test_yaml_preferred_over_yml(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "agent-shield.yaml").write_text(VALID_YAML)
        (tmp_path / "agent-shield.yml").write_text(VALID_YAML)

        result = resolve_config_path()
        assert result.name == "agent-shield.yaml"

    def test_no_config_found(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        with pytest.raises(FileNotFoundError, match="No config file found"):
            resolve_config_path()
