"""Offline docs-derived Agents API envelope replay, not a live capture.

Run: PYTHONPATH=. python examples/agents_api_replay.py
No provider SDK, credentials, network requests or external actions are used.
"""

from pathlib import Path
from tempfile import TemporaryDirectory

from agent_relationship import RelationshipTracker
from agent_relationship.adapters import AgentsAPIAdapter, HostBinding


def main():
    # Abbreviated synthetic envelope using documented fields. The original
    # message is not forwarded; the host independently compares the artifact.
    event = {
        "type": "agent.session.turn.item.done", "event_id": "evt-synthetic-message-1",
        "session_id": "sess-synthetic-1", "turn_id": "turn-synthetic-1", "output_index": 0,
        "item": {"id": "item-synthetic-1", "turn_id": "turn-synthetic-1", "type": "agent_message",
                 "sender_agent_id": "writer", "recipient_agent_id": "reviewer", "content": []},
    }
    context = {
        "action": "handoff", "goal": "Report uses the verified source version",
        "expected_result": "Review receives an artifact based on source v2",
        "result": "failed", "narrative": "Synthetic verifier found source v1 in the received artifact.",
        "observations": ["Synthetic artifact manifest source_version=v1; required=v2."],
        "evidence_refs": ["synthetic-check:artifact-manifest-1"],
        "roles": {"writer": "produce report", "reviewer": "check source provenance"},
        "dependencies": [{"resource_id": "source-v2", "state": "available"}],
    }
    adapter = AgentsAPIAdapter("sess-synthetic-1")
    binding = HostBinding("report-task-1", "writer", "reviewer", "synthetic-artifact-check")
    observation = adapter.normalize(event)
    assert observation["disposition"] == "needs_context"
    assert observation["task_result"] == "unknown"

    with TemporaryDirectory(prefix="agents-api-replay-") as tmp:
        path = Path(tmp) / "state.sqlite3"
        with RelationshipTracker(storage_path=path) as tracker:
            recorded = adapter.track(tracker, event, binding=binding, context=context)
            assert recorded.incident_id is not None
            assert recorded.assessment_source == "mock"  # Plumbing test, not semantic quality.
            assert tracker.task_view(binding.task_id)["recovery_status"] == "needs_attention"
        with RelationshipTracker(storage_path=path) as resumed:
            replayed = adapter.track(resumed, event, binding=binding, context=context)
            assert replayed.duplicate
            assert replayed.incident_id == recorded.incident_id
            assert len(resumed.incidents(status="open")) == 1
            # A successful provider turn does not resolve the original issue.
            completed = {
                "type": "agent.session.turn.completed", "event_id": "evt-synthetic-end",
                "session_id": "sess-synthetic-1", "turn_id": "turn-synthetic-1",
                "turn": {"id": "turn-synthetic-1", "session_id": "sess-synthetic-1", "agent_id": "root", "subagent_id": None},
            }
            assert adapter.normalize(completed)["task_result"] == "unknown"
            assert len(resumed.incidents(status="open")) == 1
    print("Agents API synthetic replay passed: explicit evidence, original task, durable dedup; live integration pending.")


if __name__ == "__main__":
    main()
