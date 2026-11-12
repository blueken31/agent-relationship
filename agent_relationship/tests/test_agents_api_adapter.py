"""Documented event envelopes and host-binding boundary; no network/model calls."""

import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from agent_relationship import RelationshipTracker
from agent_relationship.adapters import AgentsAPIAdapter, HostBinding
from agent_relationship.llm.mock import MockLLM


def interaction_event(item_type="agent_message", event_id="evt-message", session_id="sess-1"):
    # Minimal selected fields from the documented event/item envelope, not a
    # claimed full live provider capture. Message body is deliberately omitted.
    return {
        "type": "agent.session.turn.item.done", "event_id": event_id,
        "session_id": session_id, "turn_id": "turn-1", "output_index": 0,
        "item": {"id": "item-1", "type": item_type, "turn_id": "turn-1",
                 "sender_agent_id": "provider-reviewer", "recipient_agent_id": "provider-writer", "content": []},
    }


def host_context():
    return {
        "action": "handoff", "goal": "Use the checked source version",
        "expected_result": "Receiver uses source v2", "result": "failed",
        "narrative": "Host comparison found source v1 in the received artifact.",
        "observations": ["Artifact manifest has source_version=v1."],
        "evidence_refs": ["host-check:manifest-v2"],
        "roles": {"reviewer": "checks version", "writer": "revises artifact"},
        "dependencies": [{"resource_id": "source-v2", "state": "available"}],
    }


class CountingMock(MockLLM):
    def __init__(self):
        self.calls = 0

    def assess_interaction(self, *args, **kwargs):
        self.calls += 1
        return super().assess_interaction(*args, **kwargs)


class AgentsAPIAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = AgentsAPIAdapter("sess-1")
        self.binding = HostBinding("task-1", "reviewer", "writer", "host-artifact-verifier")

    def test_normalization_does_not_call_model_or_record(self):
        backend = CountingMock()
        with RelationshipTracker(llm=backend) as tracker:
            result = self.adapter.normalize(interaction_event())
            self.assertEqual(result["disposition"], "needs_context")
            self.assertEqual(result["task_result"], "unknown")
            self.assertEqual(result["assessment_status"], "unknown")
            self.assertIsNone(result["provider_actor_id"])
            self.assertEqual(backend.calls, 0)
            self.assertEqual(tracker.incidents(), [])

    def test_host_binding_and_evidence_are_recorded_separately(self):
        with RelationshipTracker() as tracker:
            result = self.adapter.track(tracker, interaction_event(), binding=self.binding, context=host_context())
            event = tracker.event(result.event_id)
            provenance = event["context"]["provenance"]
            self.assertEqual(event["task_id"], "task-1")
            self.assertEqual(provenance["direction"], {"initiator": "reviewer", "target": "writer"})
            self.assertEqual(provenance["host_context_source"], "host-artifact-verifier")
            self.assertEqual(provenance["provider_observation"]["task_result"], "unknown")
            self.assertEqual(event["result"], "failed")
            provider = provenance["provider_observation"]
            self.assertEqual(provider["provider_sender_id"], "provider-reviewer")
            self.assertEqual(provider["provider_recipient_id"], "provider-writer")
            self.assertIsNotNone(result.incident_id)

    def test_raw_text_tool_arguments_and_obfuscation_are_not_retained(self):
        event = interaction_event()
        event["item"].update(content=[{"text": "PRIVATE MESSAGE"}], arguments={"token": "PRIVATE TOKEN"})
        event["obfuscation"] = "padding"
        with RelationshipTracker() as tracker:
            result = self.adapter.track(tracker, event, binding=self.binding, context=host_context())
            saved = json.dumps(tracker.event(result.event_id))
            self.assertNotIn("PRIVATE", saved)
            self.assertNotIn("padding", saved)
            self.assertIn("payload_sha256", saved)

    def test_duplicates_survive_reopen_without_new_assessment(self):
        backend = CountingMock()
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.db"
            with RelationshipTracker(llm=backend, storage_path=path) as tracker:
                first = self.adapter.track(tracker, interaction_event(), binding=self.binding, context=host_context())
            with RelationshipTracker(llm=backend, storage_path=path) as tracker:
                second = AgentsAPIAdapter("sess-1").track(tracker, interaction_event(), binding=self.binding, context=host_context())
                self.assertTrue(second.duplicate)
                self.assertEqual(second.event_id, first.event_id)
                self.assertEqual(len(tracker.incidents()), 1)
            self.assertEqual(backend.calls, 1)

    def test_changed_host_binding_or_context_reuses_no_source_event(self):
        with RelationshipTracker() as tracker:
            event = interaction_event()
            self.adapter.track(tracker, event, binding=self.binding, context=host_context())
            changed = host_context()
            changed["result"] = "success"
            for binding, context in [
                (self.binding, changed),
                (HostBinding("task-2", "reviewer", "writer", "verifier"), host_context()),
                (HostBinding("task-1", "writer", "reviewer", "verifier"), host_context()),
            ]:
                with self.subTest(binding=binding), self.assertRaises(ValueError):
                    self.adapter.track(tracker, event, binding=binding, context=context)

    def test_changed_provider_content_is_detected_without_retaining_it(self):
        with RelationshipTracker() as tracker:
            event = interaction_event()
            self.adapter.track(tracker, event, binding=self.binding, context=host_context())
            event["item"]["content"] = [{"type": "output_text", "text": "Changed"}]
            with self.assertRaises(ValueError):
                self.adapter.track(tracker, event, binding=self.binding, context=host_context())

    def test_padding_change_is_still_duplicate(self):
        with RelationshipTracker() as tracker:
            event = interaction_event()
            event["obfuscation"] = "first"
            self.adapter.track(tracker, event, binding=self.binding, context=host_context())
            event["obfuscation"] = "different"
            result = self.adapter.track(tracker, event, binding=self.binding, context=host_context())
            self.assertTrue(result.duplicate)

    def test_namespacing_cannot_collide_on_separator_components(self):
        first = AgentsAPIAdapter("a:b").normalize(interaction_event(event_id="c", session_id="a:b"))
        second = AgentsAPIAdapter("a").normalize(interaction_event(event_id="b:c", session_id="a"))
        self.assertNotEqual(first["event_id"], second["event_id"])

    def test_lifecycle_does_not_create_pair_harm_or_success(self):
        with RelationshipTracker() as tracker:
            for suffix in ("completed", "failed", "cancelled"):
                event = {
                    "type": "agent.session.turn." + suffix, "event_id": "evt-" + suffix,
                    "session_id": "sess-1", "turn_id": "turn-1",
                    "turn": {"id": "turn-1", "session_id": "sess-1", "agent_id": "agent-root", "subagent_id": "sub-1"},
                }
                observed = self.adapter.normalize(event)
                self.assertEqual(observed["disposition"], "observation_only")
                self.assertEqual(observed["task_result"], "unknown")
                self.assertEqual(observed["provider_subagent_id"], "sub-1")
                with self.assertRaises(ValueError):
                    self.adapter.track(tracker, event, binding=self.binding, context=host_context())
            self.assertEqual(tracker.incidents(), [])

    def test_create_wait_interrupt_are_unassessed_coordination(self):
        with RelationshipTracker() as tracker:
            for kind in ("create_subagent_call", "wait_for_subagents_call", "interrupt_subagent_call"):
                event = interaction_event(kind)
                event["item"]["agent_id"] = "requester"
                observed = self.adapter.normalize(event)
                self.assertEqual(observed["disposition"], "observation_only")
                self.assertEqual(observed["task_result"], "unknown")
                self.assertEqual(observed["provider_actor_id"], "requester" if kind == "create_subagent_call" else None)
                with self.assertRaises(ValueError):
                    self.adapter.track(tracker, event, binding=self.binding, context=host_context())
            self.assertEqual(tracker.incidents(), [])

    def test_nested_subagent_session_and_parent_preserved(self):
        event = {"type": "agent.session.subagent.created", "event_id": "evt-create", "subagent": {
            "id": "sub-1", "session_id": "sess-1", "parent_agent_id": "agent-root", "instructions": "PRIVATE",
        }}
        observed = self.adapter.normalize(event)
        self.assertEqual(observed["provider_parent_agent_id"], "agent-root")
        self.assertEqual(observed["provider_subagent_id"], "sub-1")
        self.assertNotIn("PRIVATE", json.dumps(observed))
        event["subagent"]["session_id"] = "sess-other"
        with self.assertRaises(ValueError):
            self.adapter.normalize(event)

    def test_envelope_session_turn_mismatch_and_missing_ids_rejected(self):
        for mutate in (
            lambda e: e.update(session_id="other"),
            lambda e: e.pop("session_id"),
            lambda e: e.update(event_id=""),
            lambda e: e["item"].update(turn_id="other"),
            lambda e: e["item"].update(id=None),
        ):
            event = interaction_event()
            mutate(event)
            with self.assertRaises(ValueError):
                self.adapter.normalize(event)

    def test_future_events_and_text_deltas_are_ignored(self):
        for kind in ("future.event", "agent.session.turn.output_text.delta", "agent.session.turn.item.added"):
            result = self.adapter.normalize({"event_id": "evt-1", "type": kind})
            self.assertEqual(result["disposition"], "ignored")
        result = self.adapter.normalize(interaction_event("message"))
        self.assertEqual(result["disposition"], "ignored")

    def test_missing_host_context_and_reserved_keys_rejected_before_scoring(self):
        backend = CountingMock()
        with RelationshipTracker(llm=backend) as tracker:
            for key in ("goal", "expected_result", "observations", "evidence_refs"):
                context = host_context()
                context.pop(key)
                with self.subTest(missing=key), self.assertRaises(ValueError):
                    self.adapter.track(tracker, interaction_event(), binding=self.binding, context=context)
            for key in ("event_id", "task_id", "provenance", "scenario", "repair_plan_id", "verification_passed", "host_authorized"):
                context = host_context()
                context[key] = True
                with self.subTest(reserved=key), self.assertRaises(ValueError):
                    self.adapter.track(tracker, interaction_event(), binding=self.binding, context=context)
            self.assertEqual(backend.calls, 0)

    def test_missing_result_stays_unknown(self):
        context = host_context()
        del context["result"]
        with RelationshipTracker() as tracker:
            result = self.adapter.track(tracker, interaction_event(), binding=self.binding, context=context)
            self.assertEqual(tracker.event(result.event_id)["result"], "unknown")
            self.assertEqual(result.assessment_status, "unknown")

    def test_defensive_copy_of_inputs_and_outputs(self):
        event, context = interaction_event(), host_context()
        original_event, original_context = copy.deepcopy(event), copy.deepcopy(context)
        observed = self.adapter.normalize(event)
        observed["provider_event_id"] = "mutated"
        with RelationshipTracker() as tracker:
            result = self.adapter.track(tracker, event, binding=self.binding, context=context)
            context["observations"].append("mutated")
            self.assertEqual(tracker.event(result.event_id)["context"]["observations"], original_context["observations"])
        self.assertEqual(event, original_event)
        self.assertEqual(self.adapter.normalize(event)["provider_event_id"], "evt-message")

    def test_invalid_json_and_binding_rejected(self):
        for bad in (float("nan"), float("inf"), {1: "value"}, ("tuple",)):
            event = interaction_event()
            event["bad"] = bad
            with self.assertRaises(ValueError):
                self.adapter.normalize(event)
        with self.assertRaises(ValueError):
            HostBinding("task", "same", "same", "host")
        with self.assertRaises(ValueError):
            AgentsAPIAdapter("")


if __name__ == "__main__":
    unittest.main()
