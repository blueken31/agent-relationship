"""Versioned checkpoints restore observations, not model executions."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from agent_relationship import RelationshipTracker
from agent_relationship.llm import LLMBackend
from agent_relationship.state import StateError, export_state, restore_state


class CountingBackend(LLMBackend):
    def __init__(self):
        self.calls = 0
        self.api_key = "DO-NOT-SERIALIZE-CREDENTIAL"

    def chat(self, prompt, **kwargs):
        self.calls += 1
        return json.dumps({"status": "assessed", "impact_a": 0.4, "impact_b": 0.4,
                           "goal_status": "met", "reason": "Verified test observation"})


def event(event_id, **fields):
    return {"event_id": event_id, "task_id": "task", "action": "handoff",
            "result": "success", "narrative": "host evidence", **fields}


class TestStateCodec(unittest.TestCase):
    def roundtrip(self, original, **kwargs):
        snapshot = export_state(original)
        restored = RelationshipTracker(**kwargs)
        restore_state(restored, json.loads(json.dumps(snapshot)))
        self.assertEqual(export_state(restored), snapshot)
        return restored

    def failed(self, tracker, event_id="failed", **fields):
        return tracker.track("b", "a", event(event_id, result="failed", **fields))

    def verification(self, tracker, plan, event_id="verified", **fields):
        return tracker.track("b", "a", event(
            event_id, scenario="success", action=plan["action"],
            repair_plan_id=plan["id"], verification_passed=True, **fields))

    def test_empty_and_unknown_state_roundtrip(self):
        original = RelationshipTracker()
        self.roundtrip(original)
        original.track("b", "a", event("unknown", action="unspecified"))
        restored = self.roundtrip(original)
        self.assertEqual(restored.health("a", "b").risk, "unknown")
        self.assertEqual(restored.incidents()[0]["category"], "evidence_needed")
        self.assertIsNone(restored.event("unknown")["impact_a"])

    def test_restore_preserves_process_dependencies_and_omits_secrets(self):
        backend = CountingBackend()
        called = []
        original = RelationshipTracker(llm=backend, on_risk_change=lambda *args: called.append(args))
        original.track("a", "b", event("one"))
        self.assertEqual(backend.calls, 1)
        payload = export_state(original)
        self.assertNotIn(backend.api_key, json.dumps(payload))
        self.assertNotIn("api_key", json.dumps(payload))
        target_backend = CountingBackend()
        callback = lambda *args: called.append(args)
        target = RelationshipTracker(llm=target_backend, on_risk_change=callback)
        engine_lock = target._engine.lock
        recovery_lock = target._engine.recovery._lock
        clock = target._engine.recovery._clock
        restore_state(target, payload)
        self.assertIs(target._backend, target_backend)
        self.assertIs(target._engine.llm, target_backend)
        self.assertIs(target._on_risk_change, callback)
        self.assertIs(target._engine.lock, engine_lock)
        self.assertIs(target._engine.recovery._lock, recovery_lock)
        self.assertIs(target._engine.recovery._clock, clock)
        self.assertEqual(target_backend.calls, 0)
        self.assertEqual(called, [])

    def test_retained_evidence_and_dedup_survive_history_limit(self):
        original = RelationshipTracker(max_history=2)
        failed = self.failed(original)
        for i in range(8):
            original.track("a", "b", event(str(i)))
        restored = self.roundtrip(original, max_history=2)
        self.assertEqual(len(restored.history("a", "b")), 2)
        self.assertEqual(restored.event("failed")["initiator"], "b")
        duplicate = self.failed(restored)
        self.assertTrue(duplicate.duplicate)
        self.assertEqual(duplicate.incident_id, failed.incident_id)
        self.assertEqual(restored.health("a", "b").interaction_count, 9)
        self.assertFalse(restored.can_cooperate("a", "b", task_id="task")["can"])

    def test_active_recovery_roundtrip_and_original_task_completion(self):
        original = RelationshipTracker()
        failed = self.failed(original)
        plan = original.propose_repair(failed.incident_id)
        original.begin_repair(plan["id"], host_authorized=True)
        restored = self.roundtrip(original)
        with self.assertRaises(ValueError):
            restored.begin_repair(plan["id"], host_authorized=True)
        self.verification(restored, plan)
        completion = restored.complete_repair(plan["id"], "resolved", "verified")
        restored_again = self.roundtrip(restored)
        self.assertEqual(restored_again.complete_repair(plan["id"], "resolved", "verified"), completion)
        self.assertEqual(restored_again.incidents(status="open"), [])
        self.assertEqual(restored_again.incidents()[0]["task_id"], "task")

    def test_partial_recovery_and_cooldown_survive_restore(self):
        original = RelationshipTracker()
        failed = self.failed(original)
        plan = original.propose_repair(failed.incident_id)
        original.begin_repair(plan["id"], host_authorized=True)
        self.verification(original, plan)
        original.complete_repair(plan["id"], "partial", "verified")
        restored = self.roundtrip(original)
        self.assertEqual(restored.incidents()[0]["status"], "open")
        with self.assertRaisesRegex(ValueError, "cooldown"):
            restored.propose_repair(failed.incident_id)

    def test_cancelled_and_linked_recovery_keep_evidence_unchanged(self):
        original = RelationshipTracker()
        failed = self.failed(original, task_id=None)
        original.link_incident(failed.incident_id, "task", "Host linked original run")
        proposal = original.propose_repair(failed.incident_id)
        original.cancel_repair(proposal["id"])
        restored = self.roundtrip(original)
        self.assertIsNone(restored.event("failed")["task_id"])
        self.assertEqual(restored.incidents()[0]["task_link_reason"], "Host linked original run")
        self.assertEqual(restored.propose_repair(failed.incident_id)["task_id"], "task")

    def test_callback_failure_result_roundtrip(self):
        def fail(*args):
            raise RuntimeError("do not copy this")
        original = RelationshipTracker(on_risk_change=fail)
        self.failed(original)
        restored = self.roundtrip(original)
        result = self.failed(restored)
        self.assertTrue(result.duplicate)
        self.assertEqual(result.notification_error, "callback_failed")

    def test_direct_engine_event_without_tracker_cache_roundtrip(self):
        original = RelationshipTracker()
        original._engine.record_interaction("a", "b", event("engine"))
        self.assertEqual(original._track_results, {})
        restored = self.roundtrip(original)
        self.assertTrue(restored.track("a", "b", event("engine")).duplicate)

    def test_json_object_key_order_does_not_change_event_order(self):
        original = RelationshipTracker()
        original.track("a", "b", event("z"))
        self.failed(original, "a")
        snapshot = json.loads(json.dumps(export_state(original), sort_keys=True))
        restored = RelationshipTracker()
        restore_state(restored, snapshot)
        self.assertEqual([r["event_id"] for r in restored.history("a", "b")], ["a", "z"])

    def test_roundtrip_does_not_consume_zone_trend(self):
        original = RelationshipTracker()
        for a, b in (("a", "b"), ("b", "c")):
            for i in range(3):
                original.track(a, b, event(a + str(i), result="failed"))
        report = original.detect_moloch()
        restored = self.roundtrip(original)
        self.assertEqual(restored.detect_moloch(), report)
        restored.summary()
        self.assertEqual(restored.detect_moloch(), report)

    def test_snapshot_and_restore_do_not_alias_mutable_data(self):
        original = RelationshipTracker()
        original.track("a", "b", event("one", nested={"value": [1]}))
        payload = export_state(original)
        restored = RelationshipTracker()
        restore_state(restored, payload)
        payload["event_records"]["one"]["context"]["nested"]["value"].append(2)
        self.assertEqual(original.event("one")["context"]["nested"]["value"], [1])
        self.assertEqual(restored.event("one")["context"]["nested"]["value"], [1])

    def test_incompatible_version_or_configuration_is_rejected(self):
        original = RelationshipTracker()
        snapshot = export_state(original)
        for key, value in (("schema_version", 999), ("schema_version", True), ("scoring_version", "future")):
            broken = deepcopy(snapshot); broken[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(StateError):
                restore_state(RelationshipTracker(), broken)
        with self.assertRaisesRegex(StateError, "configuration"):
            restore_state(RelationshipTracker(max_history=2), snapshot)

    def test_malformed_state_is_rejected_without_partial_mutation(self):
        source = RelationshipTracker()
        failed = self.failed(source)
        plan = source.propose_repair(failed.incident_id)
        snapshot = export_state(source)
        target = RelationshipTracker()
        target.track("x", "y", event("existing"))
        baseline = export_state(target)
        changes = [
            lambda s: s.update(extra=True),
            lambda s: s.pop("recovery"),
            lambda s: s["event_records"]["failed"].update(impact_a=float("nan")),
            lambda s: s["events"]["failed"].update(fingerprint="changed"),
            lambda s: s["profiles"][0]["value"].update(interaction_count=99),
            lambda s: s["profiles"][0].update(key=["b", "a"]),
            lambda s: s["task_profiles"].clear(),
            lambda s: s["recovery"]["event_order"].update(failed=True),
            lambda s: s["recovery"]["incidents"][failed.incident_id]["evidence_ids"].append("missing"),
            lambda s: s["recovery"]["plans"][plan["id"]].update(grants_permission=True),
            lambda s: s["recovery"]["plans"][plan["id"]].update(state="completed"),
            lambda s: s["risk_cache"][0].update(key=["other", "a", "b"]),
            lambda s: s["track_results"]["failed"].update(balance=0.9),
        ]
        for change in changes:
            broken = deepcopy(snapshot); change(broken)
            with self.subTest(change=change), self.assertRaises(StateError):
                restore_state(target, broken)
            self.assertEqual(export_state(target), baseline)

    def test_non_json_key_types_are_not_silently_coerced(self):
        payload = export_state(RelationshipTracker())
        payload["recovery"]["incidents"][1] = {}
        with self.assertRaises(StateError):
            restore_state(RelationshipTracker(), payload)

    def test_durable_restore_rejects_in_range_derived_score_corruption_atomically(self):
        original = RelationshipTracker(max_history=2)
        self.failed(original)
        for i in range(3):
            original.track("a", "b", event(str(i)))
        # The remaining history contains only unknown observations. Validation
        # must still account for earlier assessed evidence and cumulative trust.
        for i in range(2):
            original.track("b", "a", event("unknown" + str(i), action="unsupported"))
        payload = export_state(original)
        backend = CountingBackend()
        with tempfile.TemporaryDirectory() as directory:
            with RelationshipTracker(llm=backend, max_history=2,
                                     storage_path=Path(directory) / "state.db") as target:
                restore_state(target, payload)
                baseline = export_state(target)
                self.assertEqual(baseline, payload)
                for collection in ("profiles", "task_profiles"):
                    for name in ("balance", "trust", "mutual_benefit_ratio", "power_asymmetry", "relation_type"):
                        broken = deepcopy(payload)
                        profile = broken[collection][0]["value"]
                        profile[name] = "competitive" if name == "relation_type" else (profile[name] + 0.125) % 1
                        with self.subTest(collection=collection, field=name), self.assertRaises(StateError):
                            restore_state(target, broken)
                        self.assertEqual(export_state(target), baseline)
                self.assertEqual(backend.calls, 0)

    def test_durable_restore_validates_historical_results_and_risk_cache(self):
        original = RelationshipTracker()
        self.failed(original)
        original.track("a", "b", event("later", action="unsupported"))
        payload = export_state(original)
        with tempfile.TemporaryDirectory() as directory:
            with RelationshipTracker(storage_path=Path(directory) / "state.db") as target:
                baseline = export_state(target)
                broken = deepcopy(payload)
                # Coordinated changes to both caches used to evade their mutual
                # consistency check, even though the retained event was harmful.
                for result in (broken["events"]["failed"]["result"], broken["track_results"]["failed"]):
                    result.update(balance=0.99, risk="low", relation_type="symbiotic")
                with self.assertRaisesRegex(StateError, "scores"):
                    restore_state(target, broken)
                self.assertEqual(export_state(target), baseline)
                broken = deepcopy(payload)
                broken["risk_cache"][0]["value"] = "low"
                with self.assertRaisesRegex(StateError, "risk cache"):
                    restore_state(target, broken)
                self.assertEqual(export_state(target), baseline)

    def test_durable_restore_tolerates_rounding_drift_and_preserves_saved_values(self):
        original = RelationshipTracker()
        original.track("a", "b", event("one"))
        original.track("b", "a", event("two"))
        payload = export_state(original)
        def drift(value):
            return value + 5e-13 if value < 0.5 else value - 5e-13
        for collection in ("profiles", "task_profiles"):
            for item in payload[collection]:
                for name in ("balance", "trust", "mutual_benefit_ratio", "power_asymmetry"):
                    item["value"][name] = drift(item["value"][name])
        for event_id, cached in payload["events"].items():
            for name in ("balance", "trust", "prev_balance", "confidence"):
                if cached["result"][name] is not None:
                    cached["result"][name] = drift(cached["result"][name])
                    if name in payload["track_results"][event_id]:
                        payload["track_results"][event_id][name] = cached["result"][name]
        with tempfile.TemporaryDirectory() as directory:
            with RelationshipTracker(storage_path=Path(directory) / "state.db") as target:
                restore_state(target, payload)
                self.assertEqual(export_state(target), payload)
                broken = deepcopy(payload)
                broken["profiles"][0]["value"]["balance"] += 1e-7
                with self.assertRaisesRegex(StateError, "scores"):
                    restore_state(target, broken)
                self.assertEqual(export_state(target), payload)

    def test_numeric_tolerance_cannot_change_risk_thresholds(self):
        class NeutralBackend(CountingBackend):
            def chat(self, prompt, **kwargs):
                return json.dumps({"status": "assessed", "impact_a": 0.0, "impact_b": 0.0,
                                   "goal_status": "met", "reason": "neutral evidence"})
        original = RelationshipTracker(llm=NeutralBackend())
        original.track("a", "b", event("neutral"))
        payload = export_state(original)
        self.assertEqual(payload["profiles"][0]["value"]["balance"], 0.5)
        with tempfile.TemporaryDirectory() as directory:
            with RelationshipTracker(storage_path=Path(directory) / "state.db") as target:
                baseline = export_state(target)
                for component in ("profile", "event"):
                    broken = deepcopy(payload)
                    if component == "profile":
                        broken["profiles"][0]["value"]["balance"] -= 5e-13
                    else:
                        broken["events"]["neutral"]["result"]["balance"] -= 5e-13
                        broken["track_results"]["neutral"]["balance"] -= 5e-13
                    with self.subTest(component=component), self.assertRaisesRegex(StateError, "risk threshold"):
                        restore_state(target, broken)
                    self.assertEqual(export_state(target), baseline)

    def test_advanced_memory_scores_and_repair_metadata_remain_supported(self):
        original = RelationshipTracker()
        original.track("a", "b", event("one"))
        profile = original.engine.get_relationship("a", "b")
        profile.repair_attempts = 2
        profile.last_repair_time = 123.0
        payload = export_state(original)
        with tempfile.TemporaryDirectory() as directory:
            with RelationshipTracker(storage_path=Path(directory) / "state.db") as target:
                restore_state(target, payload)
                self.assertEqual(export_state(target), payload)
        # The public low-level memory engine exposes mutable profiles. Its
        # overrides are preserved in memory, while durable mode checks evidence.
        profile.balance = 0.123
        profile.trust = 0.456
        restored = self.roundtrip(original)
        self.assertEqual(restored.health("a", "b").balance, 0.123)
        self.assertEqual(restored.engine.get_relationship("a", "b").repair_attempts, 2)

    def test_durable_validation_accepts_direct_engine_and_late_duplicate_caches(self):
        original = RelationshipTracker(max_history=1)
        self.failed(original)
        original.engine.record_interaction("a", "b", event("direct"))
        with tempfile.TemporaryDirectory() as directory:
            with RelationshipTracker(max_history=1, storage_path=Path(directory) / "state.db") as target:
                restore_state(target, export_state(original))
                self.assertEqual(export_state(target), export_state(original))
                original.track("a", "b", event("later", action="unsupported"))
                original.track("a", "b", event("direct"))
                self.assertTrue(original._track_results["direct"].duplicate)
                restore_state(target, export_state(original))
                self.assertEqual(export_state(target), export_state(original))


if __name__ == "__main__":
    unittest.main()
