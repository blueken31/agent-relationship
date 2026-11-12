"""Public-API regressions for task evidence, notifications and recovery."""

from concurrent.futures import ThreadPoolExecutor
import json
import unittest

from agent_relationship import RelationshipTracker
from agent_relationship.llm import LLMBackend


class ControlledModel(LLMBackend):
    def __init__(self, impacts=(0.4, 0.4), goal="met"):
        self.impacts = impacts
        self.goal = goal
        self.fail = False
        self.calls = 0
        self.temperatures = []

    def chat(self, prompt, **kwargs):
        self.calls += 1
        self.temperatures.append(kwargs["temperature"])
        if self.fail:
            raise TimeoutError("private-provider-detail")
        return json.dumps({"status": "assessed", "impact_a": self.impacts[0],
                           "impact_b": self.impacts[1], "goal_status": self.goal,
                           "reason": "Controlled test evidence"})


def event(event_id, task_id="task", **fields):
    return {"event_id": event_id, "task_id": task_id, "action": "handoff",
            "result": "success", "narrative": "host observation", **fields}


class TestRuntime(unittest.TestCase):
    def test_first_severe_event_notifies_and_notification_failure_is_not_write_failure(self):
        calls = []
        def callback(*args):
            calls.append(args)
            raise RuntimeError("private-hook-detail")
        tracker = RelationshipTracker(on_risk_change=callback)
        data = event("failed", result="failed")
        first = tracker.track("a", "b", data)
        retry = tracker.track("a", "b", data)
        self.assertEqual(first.risk, "critical")
        self.assertEqual(first.notification_error, "callback_failed")
        self.assertTrue(retry.duplicate)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][2], "unknown")
        self.assertEqual(tracker.health("a", "b").interaction_count, 1)
        self.assertEqual(len(tracker.incidents()), 1)

    def test_concurrent_duplicate_records_call_model_once(self):
        model = ControlledModel()
        tracker = RelationshipTracker(llm=model, temperature=0.17)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: tracker.track("a", "b", event("one")), range(16)))
        self.assertEqual(model.calls, 1)
        self.assertEqual(model.temperatures, [0.17])
        self.assertEqual(sum(not r.duplicate for r in results), 1)
        with self.assertRaises(ValueError):
            tracker.track("b", "a", event("one"))
        with self.assertRaises(ValueError):
            tracker.track("a", "b", event("one", result="failed"))
        self.assertEqual(model.calls, 1)

    def test_direct_engine_retry_is_supported(self):
        tracker = RelationshipTracker()
        tracker.engine.record_interaction("a", "b", event("one"))
        result = tracker.track("a", "b", event("one"))
        self.assertTrue(result.duplicate)
        self.assertEqual(tracker.health("a", "b").interaction_count, 1)

    def test_outage_preserves_scores_but_every_surface_shows_unknown(self):
        model = ControlledModel()
        tracker = RelationshipTracker(llm=model)
        for i in range(3):
            tracker.track("a", "b", event(str(i)))
        before = tracker.health("a", "b")
        self.assertTrue(tracker.can_cooperate("a", "b", task_id="task")["can"])
        model.fail = True
        result = tracker.track("a", "b", event("outage"))
        after = tracker.health("a", "b")
        self.assertEqual(after.balance, before.balance)
        self.assertEqual(after.trust, before.trust)
        self.assertEqual(result.risk, "unknown")
        self.assertIsNone(result.impact)
        self.assertEqual(result.error_code, "model_unavailable")
        self.assertEqual(after.confidence, 0)
        self.assertFalse(tracker.can_cooperate("a", "b", task_id="task")["can"])
        self.assertIsNone(tracker.network().pairs[("a", "b")])
        self.assertIsNone(tracker.network().avg_balance)
        self.assertNotIn("🤝", tracker.network().heatmap())
        self.assertIn("证据不足", tracker.summary())

    def test_task_scoping_does_not_transfer_good_history(self):
        tracker = RelationshipTracker()
        for i in range(4):
            tracker.track("a", "b", event(str(i), task_id="good"))
        bad = tracker.track("a", "b", event("bad", task_id="bad", result="failed"))
        self.assertEqual(bad.interaction_count, 1)
        self.assertIsNone(bad.prev_balance)
        self.assertEqual(bad.risk, "critical")
        self.assertTrue(tracker.can_cooperate("a", "b", task_id="good")["can"])
        self.assertFalse(tracker.can_cooperate("a", "b", task_id="bad")["can"])
        self.assertEqual(tracker.can_cooperate("a", "b", task_id="new")["decision"], "unknown")

    def test_sticky_issues_survive_small_successes_and_window_truncation(self):
        tracker = RelationshipTracker(max_history=2)
        bad = tracker.track("a", "b", event("bad", result="failed"))
        for i in range(20):
            tracker.track("a", "b", event(str(i)))
        self.assertGreater(tracker.health("a", "b").balance, 0.7)
        decision = tracker.can_cooperate("a", "b", task_id="task")
        self.assertFalse(decision["can"])
        self.assertIn(bad.incident_id, decision["incident_ids"])
        self.assertEqual(len(tracker.history("a", "b")), 2)
        self.assertEqual(tracker.event("bad")["result"], "failed")
        self.assertEqual(tracker.track("a", "b", event("bad", result="failed")).duplicate, True)

    def test_original_event_and_return_values_are_detached(self):
        tracker = RelationshipTracker()
        data = event("one", details={"versions": [1, 2]})
        result = tracker.track("a", "b", data)
        data["details"]["versions"].append(3)
        history = tracker.history("a", "b")
        history[0]["context"]["details"]["versions"].append(4)
        result.reason = "modified"
        self.assertEqual(tracker.event("one")["context"]["details"]["versions"], [1, 2])
        self.assertEqual(tracker.event("one")["narrative"], "host observation")
        self.assertEqual(tracker.event("one")["assessment_source"], "mock")
        self.assertNotEqual(tracker.track("a", "b", event("one", details={"versions": [1, 2]})).reason, "modified")

    def test_nested_narrative_and_result_do_not_alias_retained_evidence(self):
        tracker = RelationshipTracker()
        tracker.track("a", "b", event("nested", scenario="failure",
                                      narrative={"message": "original"}, result={"status": "failed"}))
        for snapshot in (tracker.event("nested"), tracker.history("a", "b")[0]):
            snapshot["narrative"]["message"] = "rewritten"
            snapshot["result"]["status"] = "success"
        self.assertEqual(tracker.event("nested")["narrative"], {"message": "original"})
        self.assertEqual(tracker.event("nested")["result"], {"status": "failed"})

    def test_low_score_without_specific_incident_requests_review_not_repair(self):
        tracker = RelationshipTracker(llm=ControlledModel((0.5, -0.1), goal="unknown"))
        for i in range(3):
            tracker.track("a", "b", event(str(i)))
        decision = tracker.can_cooperate("a", "b", task_id="task")
        self.assertEqual(decision["decision"], "review")
        self.assertFalse(decision["needs_repair"])
        self.assertEqual(decision["incident_ids"], [])

    def test_verified_recovery_stays_on_original_task_and_does_not_execute_work(self):
        tracker = RelationshipTracker()
        bad = tracker.track("a", "b", event("bad", result="failed"))
        plan = tracker.propose_repair(bad.incident_id)
        self.assertFalse(plan["grants_permission"])
        self.assertFalse(tracker.can_cooperate("a", "b", try_repair=True)["can"])
        with self.assertRaises(ValueError):
            tracker.begin_repair(plan["id"])
        tracker.begin_repair(plan["id"], host_authorized=True)
        with self.assertRaises(ValueError):
            tracker.begin_repair(plan["id"], host_authorized=True)
        tracker.track("a", "b", event("verified", action="verify_handoff", scenario="success",
                                      repair_plan_id=plan["id"], verification_passed=True))
        result = tracker.complete_repair(plan["id"], "resolved", "verified")
        self.assertEqual(result["task_id"], "task")
        self.assertEqual(tracker.incidents(status="open"), [])
        self.assertEqual(tracker.incidents(status="resolved")[0]["resolution_event_id"], "verified")
        self.assertEqual(tracker.complete_repair(plan["id"], "resolved", "verified"), result)

    def test_legacy_unscoped_issue_can_be_explicitly_linked(self):
        tracker = RelationshipTracker()
        bad = tracker.track("a", "b", {"event_id": "legacy", "action": "handoff", "result": "failed"})
        linked = tracker.link_incident(bad.incident_id, "task", "Host matched original run")
        self.assertEqual(linked["task_id"], "task")
        self.assertIsNone(tracker.event("legacy")["task_id"])
        self.assertEqual(tracker.propose_repair(bad.incident_id)["task_id"], "task")

    def test_moloch_queries_and_unrelated_events_preserve_trend(self):
        model = ControlledModel(impacts=(-0.1, -0.1), goal="unknown")
        tracker = RelationshipTracker(llm=model, moloch_threshold=0.4)
        for a, b in (("a", "b"), ("b", "c")):
            for i in range(3):
                tracker.track(a, b, event(a + str(i)))
        model.impacts = (-1, -1)
        tracker.track("a", "b", event("worse"))
        report = tracker.detect_moloch()
        self.assertEqual(report.zones[0].trend, "deteriorating")
        tracker.summary()
        self.assertEqual(tracker.detect_moloch(), report)
        model.impacts = (0.6, 0.6)
        tracker.track("x", "y", event("unrelated"))
        self.assertEqual(tracker.detect_moloch().zones, report.zones)

    def test_unknown_events_do_not_qualify_anomaly_sample_size(self):
        model = ControlledModel(impacts=(-1, -1))
        tracker = RelationshipTracker(llm=model)
        tracker.track("a", "b", event("bad"))
        model.fail = True
        tracker.track("a", "b", event("unknown1"))
        tracker.track("a", "b", event("unknown2"))
        self.assertEqual(tracker.detect_moloch().at_risk_pairs, [])

    def test_validation_rejects_before_recording_or_model_calls(self):
        model = ControlledModel()
        tracker = RelationshipTracker(llm=model)
        for data in (event(""), event("one", task_id=""), event("two", action=[]), event("three", value=float("nan"))):
            with self.assertRaises(ValueError):
                tracker.track("a", "b", data)
        self.assertEqual(model.calls, 0)
        self.assertIsNone(tracker.health("a", "b"))
        for value in (0, -1, True, 1.2):
            with self.assertRaises(ValueError):
                RelationshipTracker(max_history=value)
        for threshold in (True, -1, float("nan")):
            with self.assertRaises(ValueError):
                tracker.can_cooperate("a", "b", threshold=threshold)


if __name__ == "__main__":
    unittest.main()
