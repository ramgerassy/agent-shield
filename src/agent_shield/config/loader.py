from __future__ import annotations

import os
import re
from pathlib import Path

import yaml
from pydantic import ValidationError

from agent_shield.config.schema import ShieldConfig

_ENV_VAR_PATTERN = re.compile(r"\{\{([A-Z_][A-Z0-9_]*)\}\}")


def _resolve_env_vars(content: str) -> str:
    """Replace {{ENV_VAR}} placeholders with environment variable values."""

    def _replacer(match: re.Match) -> str:
        var_name = match.group(1)
        value = os.environ.get(var_name)
        if value is None:
            raise ValueError(
                f"Environment variable '{var_name}' is not set "
                f"(referenced as '{{{{{var_name}}}}}')"
            )
        return value

    return _ENV_VAR_PATTERN.sub(_replacer, content)


def resolve_config_path(config_arg: str | None = None) -> Path:
    """Resolve the config file path.

    If config_arg is provided, use it directly. Otherwise search the current
    directory for agent-shield.yaml or agent-shield.yml.
    """
    if config_arg is not None:
        path = Path(config_arg)
        if not path.is_file():
            raise FileNotFoundError(f"Config file not found: {path}")
        return path

    for name in ("agent-shield.yaml", "agent-shield.yml"):
        path = Path.cwd() / name
        if path.is_file():
            return path

    raise FileNotFoundError(
        "No config file found. Create 'agent-shield.yaml' or use --config. "
        "Run 'agent-shield init' to generate a template."
    )


def load_config(path: Path) -> ShieldConfig:
    """Load and validate an agent-shield config file."""
    if not path.is_file():
        raise FileNotFoundError(f"Config file not found: {path}")

    content = path.read_text(encoding="utf-8")
    content = _resolve_env_vars(content)

    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML in {path}: {e}") from e

    if not isinstance(data, dict):
        raise ValueError(f"Config file {path} must contain a YAML mapping, got {type(data).__name__}")

    try:
        return ShieldConfig.model_validate(data)
    except ValidationError as e:
        raise ValueError(f"Invalid config in {path}:\n{e}") from e
