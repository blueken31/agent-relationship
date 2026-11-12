"""Opt-in task evidence rules. All verification facts are supplied by the host.

These checks bind declarations; they do not authenticate a verifier, fetch a
receipt, execute checks, or grant permission. Relationship scores are not proof.
"""
from copy import deepcopy
import math

LEGACY = "legacy"
TASK_EVIDENCE = "task_evidence_v1"
ADVERSE = {"goal_violation", "material_harm", "collaboration_risk"}


def validate_policy(value):
    if value not in (LEGACY, TASK_EVIDENCE):
        raise ValueError("unsupported diagnosis_policy")


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("evidence identifiers must be nonempty strings")


def _ids(values):
    if not isinstance(values, list) or not values:
        raise ValueError("evidence identifiers must be a nonempty list")
    for value in values:
        _text(value)
    if len(set(values)) != len(values):
        raise ValueError("evidence identifiers must be unique")


def _object(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError("invalid task evidence fields")


def validate_context(context):
    """Reject malformed structured conditions before assessment or mutation."""
    failure = context.get("failure_condition")
    requirement = context.get("evidence_requirement")
    if "failure_condition" in context:
        _object(failure, {"artifact_id", "check_ids"})
        _text(failure["artifact_id"]); _ids(failure["check_ids"])
        if context.get("result") not in ("failed", "error", "timeout"):
            raise ValueError("failure_condition needs an observed failure")
    if "evidence_requirement" in context:
        _object(requirement, {"next_action", "artifact_id", "artifact_revision", "check_ids"})
        for key in ("next_action", "artifact_id", "artifact_revision"):
            _text(requirement[key])
        _ids(requirement["check_ids"])
    if failure is not None and requirement is not None:
        raise ValueError("record a failure and a missing requirement separately")
    if failure is not None or requirement is not None:
        _text(context.get("task_id"))


def condition_scope(context, category):
    if category == "evidence_needed":
        value = context.get("evidence_requirement")
        if value:
            return (value["next_action"], value["artifact_id"], value["artifact_revision"])
    if category == "task_failure":
        value = context.get("failure_condition")
        if value:
            return (value["artifact_id"],)
    return None


def validate_contract(value):
    _object(value, {"artifact_id", "artifact_revision", "check_ids", "verifier_id"})
    for key in ("artifact_id", "artifact_revision", "verifier_id"):
        _text(value[key])
    _ids(value["check_ids"])
    result = deepcopy(value)
    result["check_ids"] = sorted(result["check_ids"])
    return result


def bind_contract(contract, incident, events, action):
    if incident["category"] not in ("task_failure", "evidence_needed"):
        raise ValueError("host evidence cannot close a semantic harm or goal issue")
    if action not in ("verify_handoff", "restricted_retest"):
        raise ValueError("host evidence closure requires a bounded verification action")
    field = "failure_condition" if incident["category"] == "task_failure" else "evidence_requirement"
    for event_id in incident["evidence_ids"]:
        condition = events[event_id]["context"].get(field)
        if not condition:
            raise ValueError("original issue needs a structured evidence condition")
        if condition["artifact_id"] != contract["artifact_id"]:
            raise ValueError("verification artifact differs from original issue")
        if not set(condition["check_ids"]).issubset(contract["check_ids"]):
            raise ValueError("verification omits original required checks")
        if field == "evidence_requirement" and condition["artifact_revision"] != contract["artifact_revision"]:
            raise ValueError("verification revision differs from required revision")


def host_result_matches(plan, context, timestamp, goal_status):
    """A claimed success alone is insufficient; every predeclared check binds."""
    try:
        contract = validate_contract(plan["verification_contract"])
        value = context.get("verification")
        _object(value, {"artifact_id", "artifact_revision", "verifier_id", "receipt_id",
                        "checks", "started_at", "finished_at", "evidence_refs"})
        if any(value[key] != contract[key] for key in ("artifact_id", "artifact_revision", "verifier_id")):
            return False
        _text(value["receipt_id"]); _ids(value["evidence_refs"])
        _object(value["checks"], contract["check_ids"])
        if any(outcome != "passed" for outcome in value["checks"].values()):
            return False
        for field in ("started_at", "finished_at"):
            number = value[field]
            if type(number) not in (int, float) or not math.isfinite(number):
                return False
        return (context.get("result") == "success"
                and context.get("verification_passed") is True
                and goal_status != "violated"
                and plan["started_at"] <= value["started_at"] <= value["finished_at"] <= timestamp)
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def adverse_since_start(plan, incidents, event_order, through_sequence):
    """A new known hazard needs a fresh plan, even if it is a different category."""
    return any(
        issue["category"] in ADVERSE and issue["task_id"] == plan["task_id"]
        and issue["agents"] == plan["agents"]
        and any(plan["start_sequence"] < event_order[event_id] <= through_sequence
                for event_id in issue["evidence_ids"])
        for issue in incidents.values()
    )
