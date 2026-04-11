from __future__ import annotations

import json
from pathlib import Path

from agent_shield.config.schema import RunReport


def write_json_report(report: RunReport, output_path: str) -> Path:
    """Write the full run report to a JSON file.

    Creates parent directories if they don't exist. Returns the resolved
    output path so the caller can show it to the user.
    """
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    data = report.model_dump(mode="json")
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    return path
