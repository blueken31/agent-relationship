"""Explicit, versioned JSON checkpoints for tracker state.

Restoring a checkpoint hydrates already assessed evidence; it never replays a
model, callback or recovery action. Validation completes before any live state
is replaced. Backend configuration, credentials and process-local locks are not
part of the checkpoint.
"""

from copy import deepcopy
from dataclasses import asdict, fields
import json
import math

from .models import InteractionRecord, RelationProfile, RelationType
from .recovery import RecoveryLedger
from .types import TrackResult

SCHEMA_VERSION = 1
SCORING_VERSION = "1"


class StateError(ValueError):
    """The checkpoint is incompatible or structurally inconsistent."""


_KEYS = {
    "schema_version", "scoring_version", "config", "profiles", "task_profiles",
    "events", "event_records", "risk_cache", "track_results", "recovery",
    "zones", "previous_zones",
}
_RECOVERY_KEYS = {"incidents", "plans", "events", "event_incidents", "event_order", "sequence", "last_clock"}
_RISKS = {"unknown", "low", "medium", "high", "critical"}
_INCIDENT_FIELDS = {
    "id", "agents", "initiator", "target", "task_id", "original_task_id",
    "task_link_reason", "task_linked_at", "category", "title", "reason",
    "assessment_reason", "assessment_source", "evidence_ids", "status",
    "recommended_action", "created_at", "updated_at", "version",
    "resolution_event_id", "last_unsuccessful_at",
}
_PLAN_FIELDS = {
    "id", "incident_id", "task_id", "agents", "action", "description", "scope",
    "success_criteria", "max_attempts", "requires_host_authorization", "grants_permission",
    "state", "created_at", "started_at", "completed_at", "cancelled_at", "outcome",
    "evidence_event_id", "incident_version", "start_sequence",
}


def _check(condition, message="Invalid checkpoint structure"):
    if not condition:
        raise StateError(message)


def _object(value, keys=None):
    _check(type(value) is dict)
    _check(all(type(key) is str for key in value))
    if keys is not None:
        _check(set(value) == set(keys))
    return value


def _text(value, optional=False):
    _check((optional and value is None) or (type(value) is str and bool(value.strip())))
    return value


def _integer(value, minimum=0):
    _check(type(value) is int and value >= minimum)
    return value


def _number(value, minimum=0, maximum=None, optional=False):
    if optional and value is None:
        return value
    _check(type(value) in (int, float))
    _check(value >= minimum and (maximum is None or value <= maximum))
    try:
        _check(math.isfinite(value))
    except OverflowError:
        raise StateError("Invalid checkpoint number") from None
    return value


def _pair(value):
    _check(type(value) is list and len(value) == 2)
    a, b = map(_text, value)
    _check(a < b)
    return a, b


def _config(tracker):
    engine = tracker._engine
    return {"max_history": engine.max_history, "moloch_threshold": engine.moloch_threshold,
            "moloch_min_zone_size": engine.moloch_min_zone_size}


def _json_types(value):
    if value is None or type(value) in (str, int, bool):
        return
    if type(value) is float:
        _check(math.isfinite(value), "Checkpoint contains a nonfinite number")
    elif type(value) is list:
        for item in value:
            _json_types(item)
    elif type(value) is dict:
        _check(all(type(key) is str for key in value), "Checkpoint keys must be strings")
        for item in value.values():
            _json_types(item)
    else:
        raise StateError("Checkpoint contains unsupported JSON data")


def _json_copy(value):
    # A JSON round trip detaches all nested event data and rejects nonfinite
    # numbers or unexpected Python objects without executing deserialization code.
    try:
        _json_types(value)
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError, RecursionError):
        raise StateError("Checkpoint contains unsupported JSON data") from None


def _profile_data(profile):
    value = asdict(profile)
    value["relation_type"] = profile.relation_type.value
    return value


def export_state(tracker):
    """Return a detached JSON checkpoint. Caller serializes tracker operations."""
    engine = tracker._engine
    recovery = engine.recovery
    return _json_copy({
        "schema_version": SCHEMA_VERSION, "scoring_version": SCORING_VERSION,
        "config": _config(tracker),
        "profiles": [{"key": list(key), "value": _profile_data(value)} for key, value in engine.profiles.items()],
        "task_profiles": [{"key": list(key), "value": _profile_data(value)} for key, value in engine._task_profiles.items()],
        "events": {key: {"fingerprint": value[0], "result": value[1]} for key, value in engine._events.items()},
        "event_records": {key: asdict(value) for key, value in engine.event_records.items()},
        "risk_cache": [{"key": list(key), "value": value} for key, value in tracker._risk_cache.items()],
        "track_results": {key: asdict(value) for key, value in tracker._track_results.items()},
        "recovery": {
            "incidents": recovery._incidents, "plans": recovery._plans,
            "events": recovery._events, "event_incidents": recovery._event_incidents,
            "event_order": recovery._event_order, "sequence": recovery._sequence,
            "last_clock": recovery._last_clock,
        },
        "zones": engine._zones, "previous_zones": engine._previous_moloch_zones,
    })


def _records(data):
    _object(data)
    output = {}
    required = {field.name for field in fields(InteractionRecord)}
    for key, value in data.items():
        _text(key)
        _object(value, required)
        _check(value["event_id"] == key)
        _text(value["initiator"]); _text(value["target"])
        _check(value["initiator"] != value["target"])
        _text(value["task_id"], optional=True)
        _text(value["interaction_type"])
        _number(value["timestamp"])
        _object(value["action"]); _object(value["context"])
        _check(value["action"] == value["context"])
        _check(value["context"].get("event_id", key) == key)
        _check(value["context"].get("task_id") == value["task_id"])
        _check(value["context"].get("action", value["context"].get("type", "unknown")) == value["interaction_type"])
        _text(value["assessment_source"])
        _text(value["assessment_reason"])
        _text(value["assessment_error"], optional=True)
        output[key] = InteractionRecord(**value)
    return output


def _balance_risk(balance):
    return ("low" if balance >= 0.7 else "medium" if balance >= 0.5
            else "high" if balance >= 0.3 else "critical")


def _profile_risk(profile):
    if not profile.assessed_count or (profile.interaction_history and
            not profile.interaction_history[-1].is_assessed):
        return "unknown"
    return _balance_risk(profile.balance)


def _same_score(value, expected):
    # Different libm/Python implementations may vary by a few ulps. Hydration
    # retains the saved number; only validation uses this tight tolerance.
    if value is None or expected is None:
        return value is expected
    return math.isclose(value, expected, rel_tol=1e-12, abs_tol=1e-12)


def _derived_profile(pair, related, history_length, scoped, event_results):
    # Use only the deterministic scoring model and already assessed records.
    # No engine, assessment backend, recovery action or callback is invoked.
    # Trust depends on the full event sequence, including evidence outside the
    # rolling history; validating it costs O(events * max_history).
    derived = RelationProfile(*pair, created_at=0.0, max_history_length=history_length)
    for record in related:
        previous = derived.balance if derived.interaction_count else None
        derived.record_interaction(record)
        if scoped or record.task_id is None:
            risk = _profile_risk(derived)
            event_results[record.event_id] = {
                "balance": derived.balance, "trust": derived.trust,
                "prev_balance": previous, "relation_type": derived.relation_type.value,
                "interaction_count": derived.interaction_count, "risk": risk,
                "confidence": derived.confidence if risk != "unknown" else 0.0,
            }
    return derived


def _profiles(items, records, grouped, history_length, scoped=False, derived_results=None):
    _check(type(items) is list)
    profiles = {}
    required = {field.name for field in fields(RelationProfile)}
    for item in items:
        _object(item, {"key", "value"})
        key = item["key"]
        _check(type(key) is list and len(key) == (3 if scoped else 2))
        if scoped:
            _text(key[0])
        pair = _pair(key[-2:])
        key = tuple(key)
        _check(key not in profiles)
        data = _object(item["value"], required)
        _check((data["agent_a"], data["agent_b"]) == pair)
        _check(data["max_history_length"] == history_length)
        for name in ("balance", "trust", "mutual_benefit_ratio", "power_asymmetry"):
            _number(data[name], maximum=1)
        for name in ("created_at", "last_interaction_time", "last_repair_time"):
            _number(data[name])
        for name in ("interaction_count", "assessed_count", "symbiotic_count", "parasitic_count", "conflictual_count", "repair_attempts"):
            _integer(data[name])
        related = grouped.get(key, [])
        _check(data["interaction_count"] == len(related))
        _check(data["assessed_count"] == sum(record.is_assessed for record in related))
        _check(data["symbiotic_count"] == sum(record.is_symbiotic for record in related))
        _check(data["parasitic_count"] == sum(record.is_parasitic for record in related))
        _check(data["conflictual_count"] == sum(record.is_conflictual for record in related))
        expected_history = related[-history_length:]
        _check(data["interaction_history"] == [asdict(record) for record in expected_history])
        if related:
            _check(data["last_interaction_time"] == related[-1].timestamp)
        if derived_results is not None:
            derived = _derived_profile(pair, related, history_length, scoped, derived_results)
            for name in ("balance", "trust", "mutual_benefit_ratio", "power_asymmetry"):
                _check(_same_score(data[name], getattr(derived, name)),
                       "Checkpoint scores do not match recorded evidence")
            _check(data["relation_type"] == derived.relation_type.value,
                   "Checkpoint relationship type does not match recorded evidence")
        values = dict(data)
        values["interaction_history"] = deepcopy(expected_history)
        values["relation_type"] = RelationType(values["relation_type"])
        profiles[key] = RelationProfile(**values)
        if derived_results is not None:
            _check(_profile_risk(profiles[key]) == _profile_risk(derived),
                   "Checkpoint score drift changes a risk threshold")
    _check(set(grouped).issubset(profiles), "Checkpoint is missing a relationship profile")
    return profiles


def _result(value, record, incidents, track=False):
    required = {field.name for field in fields(TrackResult)} if track else {
        "balance", "trust", "relation_type", "impact", "interaction_count", "risk",
        "prev_balance", "event_id", "task_id", "assessment_status", "assessment_source",
        "reason", "error_code", "assessment_version", "model", "goal_status", "confidence",
        "incident_id", "duplicate",
    }
    _object(value, required)
    _check(value["event_id"] == record.event_id and value["task_id"] == record.task_id)
    _integer(value["interaction_count"], 1)
    for name in ("balance", "confidence"):
        _number(value[name], maximum=1)
    _number(value["prev_balance"], maximum=1, optional=True)
    _number(value["impact"], minimum=-1, maximum=1, optional=True)
    if not track:
        _number(value["trust"], maximum=1)
    _check(value["risk"] in _RISKS)
    RelationType(value["relation_type"])
    _check(type(value["duplicate"]) is bool)
    for field_name, record_name in (("assessment_status", "assessment_status"),
                                   ("assessment_source", "assessment_source"),
                                   ("reason", "assessment_reason"), ("error_code", "assessment_error"),
                                   ("goal_status", "goal_status")):
        _check(value[field_name] == getattr(record, record_name))
    _text(value["assessment_version"]); _text(value["model"], optional=True)
    if record.assessment_status == "unknown":
        _check(value["impact"] is None and value["risk"] == "unknown")
    else:
        _check(value["impact"] == record.net_impact / 2)
    incident_id = value["incident_id"]
    _check(incident_id is None or incident_id in incidents)
    if track:
        _check(value["risk_transition"] in (None, "first_observation", "unknown", "improved", "worsened", "stable"))
        _text(value["notification_error"], optional=True)
        return TrackResult(**value)
    return value


def _recovery(data, records):
    _object(data, _RECOVERY_KEYS)
    for field in ("incidents", "plans", "events", "event_incidents", "event_order"):
        _object(data[field])
    _integer(data["sequence"])
    _number(data["last_clock"], optional=True)
    ids = set(records)
    _check(set(data["events"]) == ids == set(data["event_incidents"]) == set(data["event_order"]))
    _check(data["sequence"] == len(ids))
    _check(all(type(value) is int for value in data["event_order"].values()))
    _check(set(data["event_order"].values()) == set(range(1, len(ids) + 1)))
    for event_id, record in records.items():
        _check(data["events"][event_id] == asdict(record))
    incidents, plans = data["incidents"], data["plans"]
    for key, issue in incidents.items():
        _text(key); _object(issue, _INCIDENT_FIELDS)
        _check(issue["id"] == key)
        pair = _pair(issue["agents"])
        _check(set((issue["initiator"], issue["target"])) == set(pair))
        _text(issue["task_id"], optional=True); _text(issue["original_task_id"], optional=True)
        _check(issue["status"] in ("open", "resolved"))
        _check(issue["category"] in ("goal_violation", "material_harm", "task_failure", "evidence_needed", "collaboration_risk"))
        _check(issue["recommended_action"] in RecoveryLedger.ACTIONS)
        _text(issue["title"]); _text(issue["assessment_reason"]); _text(issue["assessment_source"])
        _number(issue["created_at"]); _number(issue["updated_at"])
        _check(issue["updated_at"] >= issue["created_at"])
        _number(issue["last_unsuccessful_at"], optional=True)
        _number(issue["task_linked_at"], optional=True)
        linked = issue["task_linked_at"] is not None
        if linked:
            _check(issue["original_task_id"] is None and issue["task_id"] is not None)
            _text(issue["task_link_reason"])
        else:
            _check(issue["task_link_reason"] is None and issue["original_task_id"] == issue["task_id"])
        evidence = issue["evidence_ids"]
        _check(type(evidence) is list and len(evidence) > 0 and len(set(evidence)) == len(evidence))
        _integer(issue["version"], 1)
        _check(issue["version"] == len(evidence) + int(linked))
        for event_id in evidence:
            _check(event_id in records)
            record = records[event_id]
            _check(tuple(sorted((record.initiator, record.target))) == pair)
            _check(record.task_id in (issue["original_task_id"], issue["task_id"]))
            _check(data["event_incidents"][event_id] == key)
        resolution = issue["resolution_event_id"]
        _check((issue["status"] == "open" and resolution is None) or
               (issue["status"] == "resolved" and resolution in records))
    for event_id, issue_id in data["event_incidents"].items():
        _check(issue_id is None or (issue_id in incidents and event_id in incidents[issue_id]["evidence_ids"]))
    active = set()
    for key, plan in plans.items():
        _text(key); _object(plan, _PLAN_FIELDS)
        _check(plan["id"] == key and plan["incident_id"] in incidents)
        issue = incidents[plan["incident_id"]]
        _check(plan["agents"] == issue["agents"] and plan["task_id"] == issue["task_id"])
        _text(plan["task_id"])
        _check(plan["action"] in RecoveryLedger.ACTIONS)
        _check(all(plan[field] == value for field, value in RecoveryLedger.ACTIONS[plan["action"]].items()))
        _check(type(plan["max_attempts"]) is int and plan["max_attempts"] == 1)
        _check(plan["requires_host_authorization"] is True and plan["grants_permission"] is False)
        _check(plan["state"] in ("proposed", "started", "completed", "cancelled"))
        _number(plan["created_at"])
        for field in ("started_at", "completed_at", "cancelled_at"):
            _number(plan[field], optional=True)
        if plan["state"] in ("proposed", "started"):
            _check(plan["incident_id"] not in active and issue["status"] == "open")
            active.add(plan["incident_id"])
        if plan["started_at"] is None:
            _check(plan["state"] in ("proposed", "cancelled"))
            _check(plan["incident_version"] is None and plan["start_sequence"] is None)
        else:
            _check(plan["started_at"] >= plan["created_at"] and plan["state"] != "proposed")
            _integer(plan["incident_version"], 1); _integer(plan["start_sequence"])
            _check(plan["incident_version"] <= issue["version"] and plan["start_sequence"] <= data["sequence"])
        _check((plan["cancelled_at"] is not None) == (plan["state"] == "cancelled"))
        _check((plan["completed_at"] is not None) == (plan["state"] == "completed"))
        if plan["state"] == "completed":
            _check(plan["outcome"] in ("resolved", "failed", "partial"))
            event_id = plan["evidence_event_id"]
            _check(event_id in records and data["event_order"][event_id] > plan["start_sequence"])
            record = records[event_id]
            _check(record.task_id == plan["task_id"] and list(sorted((record.initiator, record.target))) == plan["agents"])
            _check(record.interaction_type == plan["action"] and record.context.get("repair_plan_id") == key)
            _check(record.timestamp >= plan["started_at"])
            if plan["outcome"] == "resolved":
                _check(issue["status"] == "resolved" and issue["resolution_event_id"] == event_id)
                _check(record.context.get("verification_passed") is True and record.context.get("result") == "success")
                _check(record.is_assessed and record.impact_a >= 0 and record.impact_b >= 0 and record.goal_status != "violated")
                _check(issue["category"] != "goal_violation" or record.goal_status == "met")
        else:
            _check(plan["outcome"] is None and plan["evidence_event_id"] is None)
    for issue in incidents.values():
        if issue["status"] == "resolved":
            _check(any(plan["incident_id"] == issue["id"] and plan["outcome"] == "resolved" for plan in plans.values()))
    return data


def _zones(items, agent_ids):
    _check(type(items) is list)
    for zone in items:
        _object(zone, {"agents", "size", "avg_balance", "trend", "severity", "first_detected"})
        _check(type(zone["agents"]) is list)
        _check(zone["agents"] == sorted(set(zone["agents"])))
        _check(set(zone["agents"]).issubset(agent_ids))
        _integer(zone["size"], 2)
        _check(zone["size"] == len(zone["agents"]))
        _number(zone["avg_balance"], maximum=1)
        _check(zone["trend"] in ("stable", "deteriorating", "improving"))
        _check(zone["severity"] in ("critical", "high", "moderate", "watch", "high_escalating", "moderate_escalating", "critical_large_scale", "watch_large_scale"))
        _check(type(zone["first_detected"]) is bool)
    return items


def restore_state(tracker, payload):
    """Validate and hydrate one checkpoint, preserving live process dependencies.

    Durable trackers require evidence-derived scores. In-memory advanced callers
    can customize exposed profile scores, so their numeric overrides are retained
    after structural validation. Repair metadata is preserved in either mode.
    Numeric validation tolerates rounding drift up to 1e-12, but drift crossing a
    risk threshold is rejected to preserve agreement between scores and labels.
    """
    try:
        state = _json_copy(payload)
        _object(state, _KEYS)
        _check(type(state["schema_version"]) is int and state["schema_version"] == SCHEMA_VERSION,
               "Unsupported checkpoint schema version")
        _check(state["scoring_version"] == SCORING_VERSION, "Unsupported checkpoint scoring version")
        _object(state["config"], {"max_history", "moloch_threshold", "moloch_min_zone_size"})
        _integer(state["config"]["max_history"], 1)
        _number(state["config"]["moloch_threshold"], maximum=1)
        _integer(state["config"]["moloch_min_zone_size"], 2)
        _check(state["config"] == _config(tracker), "Checkpoint configuration does not match this tracker")
        records = _records(state["event_records"])
        recovery = _recovery(state["recovery"], records)
        grouped, task_grouped = {}, {}
        for event_id in sorted(records, key=recovery["event_order"].get):
            record = records[event_id]
            pair = tuple(sorted((record.initiator, record.target)))
            grouped.setdefault(pair, []).append(record)
            if record.task_id is not None:
                task_grouped.setdefault((record.task_id, *pair), []).append(record)
        derived_results = {} if tracker._storage is not None else None
        profiles = _profiles(state["profiles"], records, grouped, tracker._engine.max_history,
                             derived_results=derived_results)
        task_profiles = _profiles(state["task_profiles"], records, task_grouped, tracker._engine.max_history,
                                  scoped=True, derived_results=derived_results)
        events = _object(state["events"])
        _check(set(events) == set(records))
        event_cache = {}
        for event_id, value in events.items():
            _object(value, {"fingerprint", "result"})
            record = records[event_id]
            expected = json.dumps([record.initiator, record.target, record.context], sort_keys=True, ensure_ascii=False)
            _check(value["fingerprint"] == expected, "Checkpoint event fingerprint does not match evidence")
            result = _result(value["result"], record, recovery["incidents"])
            _check(result["incident_id"] == recovery["event_incidents"][event_id])
            if derived_results is not None:
                _check(all(_same_score(result[name], expected)
                           if name in ("balance", "trust", "prev_balance", "confidence")
                           else result[name] == expected
                           for name, expected in derived_results[event_id].items()),
                       "Checkpoint event scores do not match recorded evidence")
                _check(result["risk"] == ("unknown" if not record.is_assessed else _balance_risk(result["balance"])),
                       "Checkpoint score drift changes a risk threshold")
            event_cache[event_id] = (value["fingerprint"], result)
        track_results = _object(state["track_results"])
        _check(set(track_results).issubset(records))
        results = {event_id: _result(value, records[event_id], recovery["incidents"], track=True)
                   for event_id, value in track_results.items()}
        for event_id, result in track_results.items():
            for name in set(result) & set(events[event_id]["result"]) - {"duplicate"}:
                _check(result[name] == events[event_id]["result"][name])
        risk_cache = {}
        _check(type(state["risk_cache"]) is list)
        for item in state["risk_cache"]:
            _object(item, {"key", "value"})
            key = item["key"]
            _check(type(key) is list and len(key) == 3)
            _text(key[0], optional=True); pair = _pair(key[1:])
            key = tuple(key)
            _check(key not in risk_cache and item["value"] in _RISKS)
            _check(pair in profiles if key[0] is None else key in task_profiles)
            risk_cache[key] = item["value"]
        if derived_results is not None:
            observed = {}
            for event_id in sorted(track_results, key=recovery["event_order"].get):
                record = records[event_id]
                key = (record.task_id, *sorted((record.initiator, record.target)))
                observed.setdefault(key, []).append(track_results[event_id])
            _check(set(risk_cache) == set(observed), "Checkpoint risk cache does not match tracked events")
            for key, values in observed.items():
                # A direct-engine event may be first cached by a later duplicate
                # retry. Its tracker observation order is not recorded; permit
                # any observed risk in that advanced case. Ordinary tracker-only
                # events have an unambiguous last observation.
                expected = {value["risk"] for value in values} if any(value["duplicate"] for value in values) else {values[-1]["risk"]}
                _check(risk_cache[key] in expected, "Checkpoint risk cache does not match tracked events")
        agent_ids = {agent for pair in profiles for agent in pair}
        zones = _zones(state["zones"], agent_ids)
        previous_zones = _zones(state["previous_zones"], agent_ids)
    except StateError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError, RecursionError):
        raise StateError("Invalid checkpoint structure") from None

    # No external functions are called below, and no state is replaced until
    # every component has validated. The caller holds the tracker operation lock.
    engine = tracker._engine
    engine.profiles = profiles
    engine._task_profiles = task_profiles
    engine._events = event_cache
    engine.event_records = records
    engine._zones = zones
    engine._previous_moloch_zones = previous_zones
    tracker._risk_cache = risk_cache
    tracker._track_results = results
    ledger = engine.recovery
    ledger._incidents = recovery["incidents"]
    ledger._plans = recovery["plans"]
    ledger._events = recovery["events"]
    ledger._event_incidents = recovery["event_incidents"]
    ledger._event_order = recovery["event_order"]
    ledger._sequence = recovery["sequence"]
    ledger._last_clock = recovery["last_clock"]
