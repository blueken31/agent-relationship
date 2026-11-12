"""Inject SDK observations into the same caller-owned evaluation policy.

This is an offline integration helper, not a benchmark policy or an executor.
Supply a backend factory explicitly; never derive assessments from gold labels.
The caller meters every model call and enforces the shared experiment budget.
"""

from copy import deepcopy
import hashlib
import json
import math

from agent_relationship import Assessment, RelationshipTracker
from agent_relationship.llm.base import LLMBackend


def unknown_usage():
    return {"input_tokens": None, "output_tokens": None,
            "cost": None, "currency": None}


def _digest(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _usage(value):
    if not isinstance(value, dict) or set(value) != set(unknown_usage()):
        raise ValueError("usage requires token counts, cost and currency")
    for key in ("input_tokens", "output_tokens"):
        if value[key] is not None and (type(value[key]) is not int or value[key] < 0):
            raise ValueError("token counts must be nonnegative integers or null")
    cost = value["cost"]
    if cost is not None and (type(cost) not in (int, float)
                             or not math.isfinite(cost) or cost < 0):
        raise ValueError("cost must be finite and nonnegative or null")
    currency = value["currency"]
    if currency is not None and (not isinstance(currency, str) or not currency.strip()):
        raise ValueError("currency must be a nonempty string or null")
    if cost is not None and currency is None:
        raise ValueError("measured cost requires currency")
    if cost is None and currency is not None:
        raise ValueError("unknown cost requires null currency")
    return deepcopy(value)


def _total_usage(policy_usage, sdk_usage):
    left, right = _usage(policy_usage), _usage(sdk_usage)
    result = unknown_usage()
    for key in ("input_tokens", "output_tokens"):
        if left[key] is not None and right[key] is not None:
            result[key] = left[key] + right[key]
    if left["currency"] is not None and right["currency"] is not None:
        if left["currency"] != right["currency"]:
            raise ValueError("cannot combine costs in different currencies")
        if left["cost"] is not None and right["cost"] is not None:
            result["cost"] = left["cost"] + right["cost"]
            result["currency"] = left["currency"]
            if not math.isfinite(result["cost"]):
                raise ValueError("combined cost is not finite")
    return result


class NativePolicy:
    """Call the shared decision policy with no SDK-derived context."""

    def __init__(self, decide):
        self.decide = decide

    def __call__(self, checkpoint):
        result = deepcopy(self.decide(deepcopy(checkpoint), None))
        if result.get("sdk_context") is not None:
            raise ValueError("native policy must not supply SDK context")
        result["sdk_context"] = None
        result["usage"] = _usage(result["usage"])
        return result


class SDKPolicy:
    """Replay one blind prefix, then let the same policy interpret its evidence.

    A fresh tracker/backend per checkpoint prevents future-checkpoint or other
    case state from leaking into a prefix-isolated experiment. The callback
    ``read_sdk_usage(backend)`` must include all assessment calls for that
    prefix, including retries. Missing instrumentation keeps totals unknown.

    This observer does not recreate host authorizations or recovery plans from
    natural-language facts. Its task view can therefore retain an open incident
    after a textual claim of repair. The shared policy sees the complete facts
    and must apply the declared evaluation rubric, not convert a SDK Boolean
    directly to permission or a hold decision.
    """

    def __init__(self, decide, backend_factory, read_sdk_usage=None):
        self.decide = decide
        self.backend_factory = backend_factory
        self.read_sdk_usage = read_sdk_usage

    def __call__(self, checkpoint):
        blind = deepcopy(checkpoint)
        backend = self.backend_factory()
        task = blind["task"]
        identities = {agent["agent_id"] for agent in blind["agents"]}
        observations = blind["observations"]
        records, unmapped = [], []
        with RelationshipTracker(llm=backend) as tracker:
            for index, observation in enumerate(observations):
                actor, target = observation["actor"], observation["target"]
                if actor not in identities or target not in identities or actor == target:
                    unmapped.append({"event_id": observation["event_id"],
                                     "reason": "not_an_agent_pair"})
                    continue
                result = tracker.track(actor, target, {
                    "event_id": observation["event_id"],
                    "task_id": task["task_id"],
                    "action": observation["kind"],
                    "goal": task["goal"],
                    "proposed_action": task["proposed_action"],
                    "narrative": observation["facts"],
                    "roles": blind["agents"],
                    "dependencies": blind["dependencies"],
                    "observed_prefix": observations[:index + 1],
                    "provenance": {"source": "evaluation_blind_prefix",
                                   "case_id": blind["case_id"]},
                })
                records.append(tracker.event(result.event_id))
            context = {"mode": "observe", "task_view": tracker.task_view(task["task_id"]),
                       "events": records, "unmapped_observations": unmapped,
                       "recovery_replayed": False}
        sdk_usage = (_usage(self.read_sdk_usage(backend))
                     if self.read_sdk_usage is not None else unknown_usage())
        context["assessment_usage"] = sdk_usage
        # Retain an untouched copy even if the policy mutates its arguments.
        result = deepcopy(self.decide(deepcopy(blind), deepcopy(context)))
        if result.get("sdk_context") is not None:
            raise ValueError("SDK context is supplied by the wrapper")
        result["sdk_context"] = {
            "source_input_sha256": _digest(blind),
            "source_event_ids": [event["event_id"] for event in observations],
            "payload_sha256": _digest(context), "payload": context,
        }
        result["usage"] = _total_usage(result["usage"], sdk_usage)
        return result


class _UnknownDemoBackend(LLMBackend):
    """No semantic inference or model call; only tests the integration path."""

    def chat(self, *args, **kwargs):
        raise AssertionError("offline self-check cannot call a provider")

    def assess_interaction(self, *args, **kwargs):
        return Assessment("unknown", "mock", None, None,
                          "Offline wiring check; semantic evidence unassessed.")


def main():
    import json

    checkpoint = {
        "case_id": "wiring-demo", "checkpoint_id": "wiring-demo:1",
        "task": {"task_id": "report", "goal": "Use verified data",
                 "proposed_action": "Review a handoff"},
        "agents": [{"agent_id": "writer", "role": "writer"},
                   {"agent_id": "reviewer", "role": "reviewer"}],
        "dependencies": [],
        "observations": [{"event_id": "handoff-1", "actor": "writer",
                          "target": "reviewer", "kind": "handoff",
                          "facts": "Source validation has not been supplied."}],
    }

    def decide(blind, sdk_context):
        return {"decision": "investigate", "attribution": "unknown",
                "evidence_ids": [blind["observations"][0]["event_id"]],
                "reason": "Wiring fixture only; no semantic decision measured.",
                "usage": unknown_usage()}

    native = NativePolicy(decide)(checkpoint)
    augmented = SDKPolicy(decide, _UnknownDemoBackend)(checkpoint)
    assert native["sdk_context"] is None
    assert augmented["sdk_context"]["payload"]["events"][0]["assessment_status"] == "unknown"
    print(json.dumps({"mode": "offline_wiring_check", "passed": True,
                      "semantic_benefit_measured": False,
                      "usage": augmented["usage"]}))


if __name__ == "__main__":
    main()
