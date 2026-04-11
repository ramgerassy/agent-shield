import pytest

from agent_shield.config.schema import Assertion
from agent_shield.evaluator import get_evaluator
from agent_shield.evaluator.contains import ContainsEvaluator, NotContainsEvaluator
from agent_shield.evaluator.json_schema import JsonSchemaEvaluator
from agent_shield.evaluator.length import MaxLengthEvaluator, MinLengthEvaluator
from agent_shield.evaluator.regex import RegexEvaluator


# --- contains ---


class TestContainsEvaluator:
    def test_all_mode_pass(self):
        a = Assertion(type="contains", value=["hello", "world"], mode="all")
        result = ContainsEvaluator(a).evaluate("Hello, World!")
        assert result.passed
        assert result.type == "contains"
        assert result.mode == "all"

    def test_all_mode_fail_one_missing(self):
        a = Assertion(type="contains", value=["hello", "world"], mode="all")
        result = ContainsEvaluator(a).evaluate("Hello there")
        assert not result.passed
        assert "world" in result.detail.lower()

    def test_any_mode_pass(self):
        a = Assertion(type="contains", value=["foo", "bar"], mode="any")
        result = ContainsEvaluator(a).evaluate("Just bar here")
        assert result.passed

    def test_any_mode_fail(self):
        a = Assertion(type="contains", value=["foo", "bar"], mode="any")
        result = ContainsEvaluator(a).evaluate("nothing matches")
        assert not result.passed

    def test_case_insensitive(self):
        a = Assertion(type="contains", value=["MONDAY"])
        result = ContainsEvaluator(a).evaluate("monday is the start")
        assert result.passed

    def test_default_mode_is_all(self):
        a = Assertion(type="contains", value=["a", "b"])
        assert a.mode == "all"


# --- not-contains ---


class TestNotContainsEvaluator:
    def test_pass_when_none_present(self):
        a = Assertion(type="not-contains", value=["error", "null"])
        result = NotContainsEvaluator(a).evaluate("All good here")
        assert result.passed

    def test_fail_when_one_present(self):
        a = Assertion(type="not-contains", value=["error", "null"])
        result = NotContainsEvaluator(a).evaluate("There was an error")
        assert not result.passed
        assert "error" in result.detail.lower()

    def test_case_insensitive(self):
        a = Assertion(type="not-contains", value=["ERROR"])
        result = NotContainsEvaluator(a).evaluate("an error occurred")
        assert not result.passed


# --- regex ---


class TestRegexEvaluator:
    def test_pass_simple_match(self):
        a = Assertion(type="regex", pattern=r"\d{3}")
        result = RegexEvaluator(a).evaluate("code 123 here")
        assert result.passed

    def test_fail_no_match(self):
        a = Assertion(type="regex", pattern=r"\d{3}")
        result = RegexEvaluator(a).evaluate("no digits")
        assert not result.passed

    def test_case_insensitive_flag(self):
        a = Assertion(type="regex", pattern=r"hello", flags="i")
        result = RegexEvaluator(a).evaluate("HELLO world")
        assert result.passed

    def test_invalid_regex_fails_gracefully(self):
        a = Assertion(type="regex", pattern=r"[unclosed")
        result = RegexEvaluator(a).evaluate("anything")
        assert not result.passed
        assert "invalid regex" in result.detail.lower()

    def test_unknown_flag(self):
        a = Assertion(type="regex", pattern=r"hi", flags="z")
        result = RegexEvaluator(a).evaluate("hi")
        assert not result.passed


# --- json-schema ---


class TestJsonSchemaEvaluator:
    def test_pass_valid_json_matches_schema(self):
        a = Assertion(
            type="json-schema",
            schema={"type": "object", "required": ["name"]},
        )
        result = JsonSchemaEvaluator(a).evaluate('{"name": "Alice", "age": 30}')
        assert result.passed

    def test_fail_invalid_json(self):
        a = Assertion(type="json-schema", schema={"type": "object"})
        result = JsonSchemaEvaluator(a).evaluate("not json at all")
        assert not result.passed
        assert "not valid json" in result.detail.lower()

    def test_fail_schema_mismatch(self):
        a = Assertion(
            type="json-schema",
            schema={"type": "object", "required": ["name"]},
        )
        result = JsonSchemaEvaluator(a).evaluate('{"age": 30}')
        assert not result.passed
        assert "schema validation failed" in result.detail.lower()

    def test_pass_typed_array(self):
        a = Assertion(
            type="json-schema",
            schema={"type": "array", "items": {"type": "number"}},
        )
        result = JsonSchemaEvaluator(a).evaluate("[1, 2, 3]")
        assert result.passed


# --- length ---


class TestLengthEvaluators:
    def test_min_length_pass(self):
        a = Assertion(type="min-length", value=5)
        result = MinLengthEvaluator(a).evaluate("hello world")
        assert result.passed

    def test_min_length_fail(self):
        a = Assertion(type="min-length", value=20)
        result = MinLengthEvaluator(a).evaluate("short")
        assert not result.passed

    def test_max_length_pass(self):
        a = Assertion(type="max-length", value=100)
        result = MaxLengthEvaluator(a).evaluate("short response")
        assert result.passed

    def test_max_length_fail(self):
        a = Assertion(type="max-length", value=5)
        result = MaxLengthEvaluator(a).evaluate("too long for this")
        assert not result.passed

    def test_min_length_exact(self):
        a = Assertion(type="min-length", value=5)
        result = MinLengthEvaluator(a).evaluate("12345")
        assert result.passed


# --- router ---


class TestRouter:
    @pytest.mark.parametrize(
        "assertion,expected_class",
        [
            (Assertion(type="contains", value=["x"]), ContainsEvaluator),
            (Assertion(type="not-contains", value=["x"]), NotContainsEvaluator),
            (Assertion(type="regex", pattern=r"x"), RegexEvaluator),
            (Assertion(type="json-schema", schema={"type": "object"}), JsonSchemaEvaluator),
            (Assertion(type="min-length", value=1), MinLengthEvaluator),
            (Assertion(type="max-length", value=100), MaxLengthEvaluator),
        ],
    )
    def test_router_returns_correct_evaluator(self, assertion, expected_class):
        evaluator = get_evaluator(assertion)
        assert isinstance(evaluator, expected_class)
