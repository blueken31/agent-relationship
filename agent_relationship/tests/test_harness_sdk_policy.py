"""Check evaluation isolation and accounting, not semantic model accuracy."""

from copy import deepcopy
import unittest

from agent_relationship import Assessment
from agent_relationship.llm.base import LLMBackend
from examples.harness_sdk_policy import NativePolicy, SDKPolicy, unknown_usage


class ObservingBackend(LLMBackend):
    def __init__(self):
        self.seen = []

    def chat(self, *args, **kwargs):
        raise AssertionError("no live calls")

    def assess_interaction(self, initiator, target, interaction, **kwargs):
        self.seen.append(deepcopy(interaction))
        return Assessment("unknown", "mock", None, None, "Wiring fixture, not evaluated")


class TestSDKPolicy(unittest.TestCase):
    def setUp(self):
        self.checkpoint = {
            "case_id": "case-1", "checkpoint_id": "case-1:1",
            "task": {"task_id": "task-1", "goal": "Verified deliverable",
                     "proposed_action": "Review draft"},
            "agents": [{"agent_id": "a", "role": "author"},
                       {"agent_id": "b", "role": "reviewer"}],
            "dependencies": [{"source": "a", "target": "b", "resource": "dataset"}],
            "observations": [{"event_id": "e1", "actor": "b", "target": "a",
                              "kind": "review", "facts": "Source evidence unavailable"}],
        }
        self.backends = []
        self.calls = []

    def factory(self):
        backend = ObservingBackend()
        self.backends.append(backend)
        return backend

    def policy(self, checkpoint, sdk_context):
        self.calls.append((deepcopy(checkpoint), deepcopy(sdk_context)))
        return {"decision": "continue", "attribution": "none",
                "evidence_ids": ["e1"], "reason": "Host test policy decision",
                "usage": unknown_usage()}

    def test_same_blind_input_and_host_policy_are_preserved(self):
        before = deepcopy(self.checkpoint)
        native = NativePolicy(self.policy)(self.checkpoint)
        sdk = SDKPolicy(self.policy, self.factory)(self.checkpoint)
        self.assertEqual(self.calls[0][0], self.calls[1][0])
        self.assertIsNone(self.calls[0][1])
        self.assertEqual(before, self.checkpoint)
        # Unknown SDK evidence does not override a host decision into a hold.
        self.assertEqual(native["decision"], sdk["decision"])
        event = sdk["sdk_context"]["payload"]["events"][0]
        self.assertEqual(event["assessment_status"], "unknown")
        self.assertFalse(sdk["sdk_context"]["payload"]["recovery_replayed"])
        self.assertEqual(self.backends[0].seen[0]["roles"], before["agents"])
        self.assertEqual(self.backends[0].seen[0]["dependencies"], before["dependencies"])

    def test_prefix_assessment_cannot_see_future_events(self):
        self.checkpoint["observations"].append({
            "event_id": "e2", "actor": "a", "target": "b",
            "kind": "handoff", "facts": "Later observation"})
        wrapper = SDKPolicy(self.policy, self.factory)
        wrapper(self.checkpoint)
        self.assertEqual(len(self.backends[0].seen[0]["observed_prefix"]), 1)
        self.assertEqual(len(self.backends[0].seen[1]["observed_prefix"]), 2)
        wrapper({**self.checkpoint, "observations": self.checkpoint["observations"][:1]})
        self.assertEqual(len(self.backends), 2)
        self.assertEqual(len(self.backends[1].seen), 1)
        self.assertEqual(len(self.calls[1][1]["events"]), 1)

    def test_unknown_or_shared_resource_is_not_invented_as_agent(self):
        self.checkpoint["observations"].insert(0, {
            "event_id": "outage", "actor": "dataset-service", "target": "a",
            "kind": "outage", "facts": "Shared dependency inaccessible"})
        result = SDKPolicy(self.policy, self.factory)(self.checkpoint)
        self.assertEqual(len(self.backends[0].seen), 1)
        self.assertEqual(result["sdk_context"]["payload"]["unmapped_observations"],
                         [{"event_id": "outage", "reason": "not_an_agent_pair"}])
        self.assertEqual(len(self.backends[0].seen[0]["observed_prefix"]), 2)

    def test_unmetered_sdk_calls_keep_total_usage_unknown(self):
        def measured(blind, context):
            result = self.policy(blind, context)
            result["usage"] = {"input_tokens": 20, "output_tokens": 10,
                               "cost": 0.1, "currency": "USD"}
            return result
        result = SDKPolicy(measured, self.factory)(self.checkpoint)
        self.assertEqual(result["usage"], unknown_usage())
        result = SDKPolicy(measured, self.factory, lambda backend: {
            "input_tokens": 5, "output_tokens": 3, "cost": 0.02, "currency": "USD"
        })(self.checkpoint)
        self.assertEqual(result["usage"]["input_tokens"], 25)
        self.assertEqual(result["usage"]["output_tokens"], 13)
        self.assertAlmostEqual(result["usage"]["cost"], 0.12)

    def test_partial_measurement_and_currency_mismatch(self):
        def measured(blind, context):
            result = self.policy(blind, context)
            result["usage"] = {"input_tokens": 20, "output_tokens": 10,
                               "cost": 0.1, "currency": "USD"}
            return result
        result = SDKPolicy(measured, self.factory, lambda backend: {
            "input_tokens": 2, "output_tokens": None, "cost": None, "currency": None
        })(self.checkpoint)
        self.assertEqual(result["usage"], {
            "input_tokens": 22, "output_tokens": None, "cost": None, "currency": None})
        with self.assertRaises(ValueError):
            SDKPolicy(measured, self.factory, lambda backend: {
                "input_tokens": 2, "output_tokens": 1, "cost": 0.1, "currency": "JPY"
            })(self.checkpoint)

    def test_policy_mutations_cannot_rewrite_retained_sdk_evidence(self):
        def mutate(blind, context):
            result = self.policy(blind, context)
            context["events"].clear()
            blind["observations"].clear()
            return result
        result = SDKPolicy(mutate, self.factory)(self.checkpoint)
        self.assertEqual(len(result["sdk_context"]["payload"]["events"]), 1)
        self.assertEqual(len(self.checkpoint["observations"]), 1)


if __name__ == "__main__":
    unittest.main()
