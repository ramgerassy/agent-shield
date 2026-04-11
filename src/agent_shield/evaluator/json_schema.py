from __future__ import annotations

import json

from jsonschema import ValidationError as JSONSchemaValidationError
from jsonschema import validate as jsonschema_validate

from agent_shield.config.schema import AssertionResult
from agent_shield.evaluator.base import BaseEvaluator


class JsonSchemaEvaluator(BaseEvaluator):
    """Pass if the response (parsed as JSON) validates against the schema."""

    def evaluate(self, response: str) -> AssertionResult:
        schema = self.assertion.schema_

        try:
            parsed = json.loads(response)
        except json.JSONDecodeError as e:
            return AssertionResult(
                type="json-schema",
                expected=schema,
                passed=False,
                detail=f"Response is not valid JSON: {e.msg}",
            )

        try:
            jsonschema_validate(parsed, schema)
        except JSONSchemaValidationError as e:
            return AssertionResult(
                type="json-schema",
                expected=schema,
                passed=False,
                detail=f"Schema validation failed: {e.message}",
            )

        return AssertionResult(
            type="json-schema",
            expected=schema,
            passed=True,
            detail="Response matches schema",
        )
