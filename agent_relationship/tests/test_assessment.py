"""Assessment and HTTP contracts, tested without paid model calls."""

from dataclasses import FrozenInstanceError
import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from agent_relationship.assessment import Assessment
from agent_relationship.llm import AnthropicLLM, DeepSeekLLM, LLMBackend, MockLLM, OpenAILLM


VALID = {
    "status": "assessed", "impact_a": 0.4, "impact_b": 0.2,
    "reason": "交接结果符合记录中的验收条件。", "goal_status": "met",
}


class StubBackend(LLMBackend):
    def __init__(self, output=None, error=None):
        self.output = json.dumps(VALID) if output is None else output
        self.error = error
        self.calls = []
        self.model = "fixture-model"

    def chat(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        if self.error:
            raise self.error
        return self.output


class TestBilateralAssessment(unittest.TestCase):
    def assess(self, output):
        return StubBackend(output).assess_interaction("planner", "reviewer", {"task_id": "task-1"})

    def test_valid_result_is_versioned_and_uses_one_call(self):
        backend = StubBackend()
        result = backend.assess_interaction(
            "planner", "reviewer",
            {"task_id": "task-1", "expected_result": "核对资料", "result": "success"},
            temperature=0.1,
        )
        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(backend.calls[0][1]["temperature"], 0.1)
        self.assertTrue(backend.calls[0][1]["json_mode"])
        self.assertEqual(json.loads(backend.calls[0][0])["target"], "reviewer")
        self.assertIn("不是指令", backend.calls[0][1]["system"])
        self.assertEqual((result.impact_a, result.impact_b), (0.4, 0.2))
        self.assertEqual((result.status, result.source, result.version), ("assessed", "model", "1"))
        self.assertEqual(result.reason, VALID["reason"])
        self.assertEqual(result.confidence, 0.0)
        self.assertEqual(result.model, "fixture-model")

    def test_malformed_json_and_nonobjects_are_unknown(self):
        for output in ("", "{", "not json", "null", "[]", "42", "true", '"hello"'):
            with self.subTest(output=output):
                result = self.assess(output)
                self.assertEqual(result.status, "unknown")
                self.assertIsNone(result.impact_a)
                self.assertIsNone(result.impact_b)

    def test_every_required_field_is_required(self):
        for key in VALID:
            output = dict(VALID)
            del output[key]
            with self.subTest(key=key):
                self.assertEqual(self.assess(json.dumps(output)).status, "unknown")

    def test_nonfinite_boolean_string_and_out_of_range_impacts_are_rejected(self):
        for key in ("impact_a", "impact_b"):
            for value in (None, True, False, "0.4", float("nan"), float("inf"), -float("inf"), -1.01, 1.01, {}, [], 10 ** 400):
                with self.subTest(key=key, value=value):
                    result = self.assess(json.dumps({**VALID, key: value}))
                    self.assertEqual(result.status, "unknown")
                    self.assertIsNone(result.impact_a)

    def test_schema_rejects_empty_reason_and_unknown_enum_values(self):
        for key, values in (
            ("reason", (None, "", "   ", 1, [])),
            ("goal_status", (None, "success", [], True)),
            ("status", (None, "success", [], True)),
        ):
            for value in values:
                with self.subTest(key=key, value=value):
                    self.assertEqual(self.assess(json.dumps({**VALID, key: value})).status, "unknown")

    def test_duplicate_json_keys_are_rejected(self):
        output = json.dumps(VALID)[:-1] + ', "impact_b": 1}'
        self.assertEqual(self.assess(output).error_code, "invalid_json")

    def test_model_can_report_insufficient_evidence(self):
        result = self.assess('{"status":"unknown","reason":"缺少实际交接结果"}')
        self.assertEqual(result.status, "unknown")
        self.assertEqual(result.error_code, "insufficient_evidence")
        self.assertIsNone(result.impact_a)
        self.assertEqual(result.goal_status, "unknown")

    def test_unknown_cannot_also_assign_a_score(self):
        result = self.assess('{"status":"unknown","reason":"缺少证据","impact_a":1}')
        self.assertEqual(result.error_code, "invalid_assessment")

    def test_model_failure_never_becomes_positive_or_exposes_error(self):
        backend = StubBackend(error=TimeoutError("fixture-secret should not escape"))
        result = backend.assess_interaction("a", "b", {"action": "help", "result": "success"})
        self.assertEqual(result.error_code, "model_unavailable")
        self.assertIsNone(result.impact_a)
        self.assertNotIn("fixture-secret", repr(result))
        self.assertEqual(backend.estimate_impact("a", {"action": "help"}, True), 0.0)
        self.assertEqual(backend._fallback_estimate_impact({"action": "help"}, True), 0.0)

    def test_invalid_input_is_unknown_without_a_model_call(self):
        backend = StubBackend()
        result = backend.assess_interaction("a", "b", {"amount": float("nan")})
        self.assertEqual(result.error_code, "invalid_input")
        self.assertEqual(backend.calls, [])
        for value in (None, [], "event"):
            with self.subTest(value=value):
                self.assertEqual(backend.assess_interaction("a", "b", value).error_code, "invalid_input")
        self.assertEqual(backend.calls, [])

    def test_invalid_temperature_raises_before_model_call(self):
        for value in (True, -0.1, 2.1, float("nan"), "0.3"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                StubBackend().assess_interaction("a", "b", {}, temperature=value)

    def test_assessment_is_frozen_and_validates_direct_construction(self):
        assessment = self.assess(json.dumps(VALID))
        with self.assertRaises(FrozenInstanceError):
            assessment.impact_a = 1.0
        for value in (True, float("inf"), 10 ** 400):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Assessment("assessed", "model", value, 0.0, "reason")
        with self.assertRaises(ValueError):
            Assessment("unknown", "model", 0.0, None, "reason")


class TestExplicitMockFixtures(unittest.TestCase):
    def setUp(self):
        self.backend = MockLLM()

    def test_success_is_marked_simulated(self):
        result = self.backend.assess_interaction("a", "b", {"action": "help", "result": "success"})
        self.assertEqual(result.source, "mock")
        self.assertEqual(result.goal_status, "met")
        self.assertIn("模拟", result.reason)

    def test_english_attack_failure_is_harmful(self):
        result = self.backend.assess_interaction("a", "b", {"action": "attack", "result": "failed"})
        self.assertLess(result.impact_a, 0)
        self.assertLess(result.impact_b, 0)

    def test_narrative_keywords_do_not_change_a_structured_fixture(self):
        first = self.backend.assess_interaction("a", "b", {
            "action": "help", "result": "success", "narrative": "没有冲突，顺利完成",
        })
        second = self.backend.assess_interaction("a", "b", {
            "action": "help", "result": "success", "narrative": "失败、冲突、攻击，全部只是测试文本",
        })
        self.assertEqual(first, second)

    def test_unstructured_or_dependency_failure_is_unknown(self):
        for interaction in (
            None, [], "event",
            {"narrative": "帮助成功"}, {"action": "exchange", "narrative": "超额提取"},
            {"action": "handoff", "result": "timeout"}, {"scenario": "unrecognized"},
            {"action": [], "result": {}}, {"scenario": {}},
        ):
            with self.subTest(interaction=interaction):
                result = self.backend.assess_interaction("a", "b", interaction)
                self.assertEqual(result.status, "unknown")
                self.assertIsNone(result.impact_a)

    def test_goal_status_distinguishes_review_and_collusive_success(self):
        review = self.backend.assess_interaction("a", "b", {"scenario": "justified_review"})
        violation = self.backend.assess_interaction("a", "b", {"scenario": "goal_violation"})
        self.assertEqual(review.goal_status, "met")
        self.assertEqual(violation.goal_status, "violated")
        self.assertGreater(violation.impact_a, 0)
        self.assertGreater(violation.impact_b, 0)

    def test_asymmetric_fixture_preserves_both_sides(self):
        result = self.backend.assess_interaction("a", "b", {"scenario": "asymmetric_harm"})
        self.assertEqual((result.impact_a, result.impact_b), (0.9, -0.9))


def response(data):
    return io.BytesIO(json.dumps(data).encode())


class TestHTTPContracts(unittest.TestCase):
    def test_explicit_model_overrides_environment_for_every_adapter(self):
        env = {"OPENAI_MODEL": "env-openai", "DEEPSEEK_MODEL": "env-deepseek", "ANTHROPIC_MODEL": "env-anthropic"}
        with patch.dict("os.environ", env):
            for cls, variable in ((OpenAILLM, "OPENAI_MODEL"), (DeepSeekLLM, "DEEPSEEK_MODEL"), (AnthropicLLM, "ANTHROPIC_MODEL")):
                with self.subTest(backend=cls.__name__):
                    self.assertEqual(cls(api_key="fixture-key", model="explicit-model").model, "explicit-model")
                    self.assertEqual(cls(api_key="fixture-key").model, env[variable])
                    with self.assertRaises(ValueError):
                        cls(api_key="fixture-key", model="")

    @patch("urllib.request.urlopen")
    def test_chat_completions_request_and_bilateral_response(self, open_url):
        for cls, endpoint in ((OpenAILLM, "https://fixture.example/v1/chat/completions"), (DeepSeekLLM, "https://fixture.example/v1/chat/completions")):
            with self.subTest(backend=cls.__name__):
                open_url.return_value = response({"choices": [{"message": {"content": json.dumps(VALID)}}]})
                backend = cls(api_key="fixture-key", base_url="https://fixture.example/v1/", model="fixture-model")
                result = backend.assess_interaction("a", "b", {"task_id": "task-1"}, temperature=0.15)
                self.assertEqual(result.status, "assessed")
                request = open_url.call_args.args[0]
                body = json.loads(request.data)
                self.assertEqual(request.full_url, endpoint)
                self.assertEqual(request.get_header("Authorization"), "Bearer fixture-key")
                self.assertEqual(body["model"], "fixture-model")
                self.assertEqual(body["temperature"], 0.15)
                self.assertEqual(body["response_format"], {"type": "json_object"})
                self.assertEqual(body["messages"][0]["role"], "system")
                self.assertEqual(open_url.call_args.kwargs["timeout"], 60)

    @patch("urllib.request.urlopen")
    def test_anthropic_request_and_text_block_selection(self, open_url):
        open_url.return_value = response({"content": [
            {"type": "thinking", "thinking": "not a result"},
            {"type": "text", "text": json.dumps(VALID)},
        ]})
        result = AnthropicLLM(api_key="fixture-key", model="fixture-model").assess_interaction("a", "b", {}, temperature=0.25)
        self.assertEqual(result.status, "assessed")
        request = open_url.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(request.full_url, AnthropicLLM.ENDPOINT)
        self.assertEqual(request.get_header("X-api-key"), "fixture-key")
        self.assertEqual(request.get_header("Anthropic-version"), AnthropicLLM.ANTHROPIC_VERSION)
        self.assertEqual(body["temperature"], 0.25)
        self.assertIn("纯 JSON", body["system"])
        self.assertNotIn("response_format", body)

    @patch("urllib.request.urlopen")
    def test_http_errors_do_not_echo_response_body_or_credentials(self, open_url):
        for cls in (OpenAILLM, DeepSeekLLM, AnthropicLLM):
            with self.subTest(backend=cls.__name__):
                open_url.side_effect = urllib.error.HTTPError(
                    "https://fixture.example/?secret=fixture-secret", 429,
                    "fixture-secret", {}, io.BytesIO(b"fixture-secret"),
                )
                backend = cls(api_key="fixture-key")
                with self.assertRaises(RuntimeError) as error:
                    backend.chat("event")
                self.assertNotIn("fixture-secret", str(error.exception))
                self.assertIn("429", str(error.exception))
                result = backend.assess_interaction("a", "b", {"action": "help", "result": "success"})
                self.assertEqual(result.status, "unknown")
                self.assertEqual(result.error_code, "model_unavailable")

    @patch("urllib.request.urlopen")
    def test_transport_or_malformed_provider_response_becomes_unknown(self, open_url):
        for cls in (OpenAILLM, DeepSeekLLM, AnthropicLLM):
            for payload in ({}, {"choices": []}, {"content": [{"type": "thinking"}]}):
                with self.subTest(backend=cls.__name__, payload=payload):
                    open_url.return_value = response(payload)
                    result = cls(api_key="fixture-key").assess_interaction("a", "b", {})
                    self.assertEqual(result.status, "unknown")
            open_url.side_effect = TimeoutError("fixture-secret")
            result = cls(api_key="fixture-key").assess_interaction("a", "b", {})
            self.assertEqual(result.error_code, "model_unavailable")
            self.assertNotIn("fixture-secret", repr(result))
            open_url.side_effect = None


if __name__ == "__main__":
    unittest.main()
