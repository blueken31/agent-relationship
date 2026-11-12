"""Conservative observation boundary for the OpenAI Agents API event stream.

This module does not open streams, execute tools, authorize work or infer task
success from provider lifecycle events. See docs/AGENTS_API_ADAPTER.md.
"""

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Dict


_ITEM_DONE = "agent.session.turn.item.done"
_COORDINATION = frozenset({
    "create_subagent_call", "send_subagent_input_call",
    "wait_for_subagents_call", "interrupt_subagent_call", "agent_message",
})
# These are potential interaction observations only after the host supplies
# explicit task binding, selected evidence, roles and actual outcome context.
_INTERACTIONS = frozenset({"send_subagent_input_call", "agent_message"})
_TURN_EVENTS = frozenset({
    "agent.session.turn.completed", "agent.session.turn.failed",
    "agent.session.turn.cancelled",
})
_SUBAGENT_EVENTS = frozenset({
    "agent.session.subagent.created", "agent.session.subagent.active",
    "agent.session.subagent.closed",
})
_CONTEXT_FIELDS = frozenset({
    "action", "goal", "expected_result", "narrative", "result",
    "observations", "evidence_refs", "roles", "dependencies", "third_party_effects",
})


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a nonempty string")
    return value


def _json_copy(value: Any) -> Any:
    def validate(node):
        if node is None or type(node) in (str, bool, int, float):
            return
        if type(node) is list:
            for child in node:
                validate(child)
            return
        if type(node) is dict and all(type(key) is str for key in node):
            for child in node.values():
                validate(child)
            return
        raise ValueError("expected JSON values with string object keys")
    try:
        validate(value)
        return json.loads(json.dumps(value, allow_nan=False, ensure_ascii=False))
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("expected finite JSON data") from exc


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class HostBinding:
    """Host-owned task/pair identity, never inferred from display names.

    ``evidence_source`` names the host's verifier or evidence collection process;
    it is provenance supplied by the caller, not authentication by this adapter.
    """

    task_id: str
    initiator: str
    target: str
    evidence_source: str

    def __post_init__(self):
        for field in ("task_id", "initiator", "target", "evidence_source"):
            _text(getattr(self, field), field)
        if self.initiator == self.target:
            raise ValueError("binding requires two distinct agents")


class AgentsAPIAdapter:
    """Normalize dictionaries from ``event.model_dump(mode='json')``.

    Normalization is pure and never calls a model. ``track`` is a separate,
    explicit SDK write and may call the tracker's configured assessment backend.
    """

    def __init__(self, session_id: str):
        self._session_id = _text(session_id, "session_id")

    def normalize(self, event: Dict) -> Dict:
        """Return selected metadata, or an ignored/observation-only disposition.

        Unknown future event types are ignored. Malformed recognized events and
        session/turn mismatches raise without mutating a tracker. Missing context
        remains unknown; lifecycle completion never becomes a business result.
        """
        if not isinstance(event, dict):
            raise ValueError("event must be a JSON object")
        raw = _json_copy(event)
        event_type = _text(raw.get("type"), "event.type")
        provider_id = _text(raw.get("event_id"), "event.event_id")
        supplied_session = raw.get("session_id")
        if supplied_session is not None and supplied_session != self._session_id:
            raise ValueError("event belongs to a different session")
        # Random stream padding may change without changing the observation.
        raw.pop("obfuscation", None)
        result = {
            "adapter_version": "1", "provider": "openai_agents_api",
            "session_id": self._session_id, "provider_event_id": provider_id,
            "event_id": "agents_api_" + _digest([self._session_id, provider_id]),
            "event_type": event_type, "payload_sha256": _digest(raw),
            "disposition": "ignored", "reason": "unsupported_event",
            "assessment_status": "unknown", "task_result": "unknown",
            "turn_id": None, "item_id": None, "item_type": None,
            "provider_actor_id": None, "provider_subagent_id": None,
            "provider_parent_agent_id": None,
            "provider_sender_id": None, "provider_recipient_id": None,
        }
        if event_type == _ITEM_DONE:
            if supplied_session != self._session_id:
                raise ValueError("item event requires matching session_id")
            item = raw.get("item")
            if not isinstance(item, dict):
                raise ValueError("item event requires an item object")
            item_id = _text(item.get("id"), "item.id")
            item_type = _text(item.get("type"), "item.type")
            turn_id = raw.get("turn_id")
            if turn_id is not None:
                _text(turn_id, "turn_id")
            if item.get("turn_id") is not None and item["turn_id"] != turn_id:
                raise ValueError("item and event turn_id disagree")
            result.update(turn_id=turn_id, item_id=item_id, item_type=item_type)
            if item_type not in _COORDINATION:
                return result
            result.update(disposition="observation_only", reason="coordination_is_not_task_outcome")
            if item_type == "create_subagent_call" and item.get("agent_id") is not None:
                result["provider_actor_id"] = _text(item["agent_id"], "item.agent_id")
            if item_type in _INTERACTIONS:
                for key in ("sender_agent_id", "recipient_agent_id"):
                    if item.get(key) is not None:
                        mapped = "provider_sender_id" if key == "sender_agent_id" else "provider_recipient_id"
                        result[mapped] = _text(item[key], f"item.{key}")
                result.update(disposition="needs_context", reason="host_binding_and_evidence_required")
        elif event_type in _SUBAGENT_EVENTS:
            subagent = raw.get("subagent")
            if not isinstance(subagent, dict) or subagent.get("session_id") != self._session_id:
                raise ValueError("subagent requires matching session_id")
            result.update(
                disposition="observation_only", reason="lifecycle_is_not_task_outcome",
                provider_subagent_id=_text(subagent.get("id"), "subagent.id"),
                provider_parent_agent_id=_text(subagent.get("parent_agent_id"), "subagent.parent_agent_id"),
            )
        elif event_type in _TURN_EVENTS:
            turn = raw.get("turn")
            if supplied_session != self._session_id or not isinstance(turn, dict):
                raise ValueError("turn event requires matching session and turn")
            turn_id = _text(raw.get("turn_id"), "turn_id")
            if turn.get("id") != turn_id or turn.get("session_id") != self._session_id:
                raise ValueError("turn identity disagrees with event")
            subagent_id = turn.get("subagent_id")
            if subagent_id is not None:
                _text(subagent_id, "turn.subagent_id")
            result.update(
                disposition="observation_only", reason="lifecycle_is_not_task_outcome",
                turn_id=turn_id, provider_subagent_id=subagent_id,
                provider_actor_id=_text(turn.get("agent_id"), "turn.agent_id"),
            )
        return result

    def track(self, tracker, event: Dict, *, binding: HostBinding, context: Dict):
        """Record one explicitly bound interaction using host-selected evidence.

        Does not approve or complete a recovery plan. Authorization and recovery
        verification fields are not accepted; use the host's existing recovery
        path for independently verified outcomes. Repeated identical input is
        deduplicated by the tracker, including across SQLite reopen.
        """
        observation = self.normalize(event)
        if observation["disposition"] != "needs_context":
            raise ValueError("event is not a supported interaction observation")
        if not isinstance(binding, HostBinding):
            raise ValueError("an explicit HostBinding is required")
        if not isinstance(context, dict):
            raise ValueError("host context must be a JSON object")
        data = _json_copy(context)
        if set(data) - _CONTEXT_FIELDS:
            raise ValueError("unsupported host context fields")
        for key in ("action", "goal", "expected_result", "narrative"):
            _text(data.get(key), f"context.{key}")
        for key in ("observations", "evidence_refs"):
            values = data.get(key)
            if not isinstance(values, list) or not values:
                raise ValueError(f"context.{key} must be a nonempty list")
            for value in values:
                _text(value, f"context.{key} entry")
        if "result" in data:
            _text(data["result"], "context.result")
        else:
            data["result"] = "unknown"
        data.update(
            event_id=observation["event_id"], task_id=binding.task_id,
            provenance={
                "adapter": "openai_agents_api", "adapter_version": "1",
                "provider_observation": observation,
                "host_context_source": binding.evidence_source,
                "binding_source": "host", "outcome_source": "host",
                "direction": {"initiator": binding.initiator, "target": binding.target},
                "verification": "not_performed_by_adapter",
            },
        )
        return tracker.track(binding.initiator, binding.target, data)
