# agent-shield

CLI tool that tests AI agent systems for functional correctness, resilience, and security. Think "pytest for AI agents."

## Quick Start

```bash
# Install
uv venv
uv pip install -e ".[dev]"

# Generate a config template
agent-shield init

# Run tests against your agent
agent-shield run

# CI mode (exit code 1 if below threshold)
agent-shield run --ci
```

## How It Works

1. Define test cases in `agent-shield.yaml` — prompts + assertions
2. agent-shield sends prompts to your agent's HTTP endpoint
3. Responses are evaluated against deterministic assertions (contains, regex, JSON schema, etc.)
4. A scored report is generated — pass/fail based on configurable threshold

## License

AGPL-3.0-or-later
