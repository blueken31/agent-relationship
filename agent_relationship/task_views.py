"""Read-only, task-first projections of recorded collaboration evidence.

These cards describe SDK bookkeeping, not a host's execution status or
authorization. Callers supply one consistent snapshot and a read-only clock;
projection never creates a recovery plan or changes the ledger's clock.
"""

from copy import deepcopy
import math


_WAITING = {"awaiting_result", "cooldown"}
_STATE_PRIORITY = {
    "evidence_missing": 0, "needs_reconciliation": 1, "needs_task_link": 2,
    "awaiting_authorization": 3, "ready_for_retry": 4, "cancelled": 4,
    "needs_plan": 4, "cooldown": 5, "awaiting_result": 6, "resolved": 7,
}
_CATEGORY_PRIORITY = {
    "goal_violation": 0, "material_harm": 1, "task_failure": 2,
    "collaboration_risk": 3, "evidence_needed": 4,
}


def _text(value, limit=240):
    """Keep a short plain-text summary; detailed input remains in event()."""
    if not isinstance(value, str):
        return ""
    value = " ".join(value.split())
    return value if len(value) <= limit else value[:limit - 1] + "…"


def _time(value):
    return (float(value) if type(value) in (int, float)
            and math.isfinite(value) and value >= 0 else 0.0)


def _action(kind, label, incident=None, plan=None):
    return {
        "kind": kind, "label": label,
        "incident_id": incident["id"] if incident else None,
        "plan_id": plan["id"] if plan else None,
        "grants_permission": False,
    }


def _plan_order(plan):
    return (max(_time(plan.get(name)) for name in
                ("created_at", "started_at", "completed_at", "cancelled_at")),
            _time(plan.get("created_at")))


def _select_plan(incident, related_plans):
    """Use recorded chronology only; random IDs cannot identify a later plan."""
    active = [p for p in related_plans if p["state"] in ("proposed", "started")]
    candidates = active or related_plans
    if not active and incident["status"] == "resolved" and incident.get("resolution_event_id"):
        resolved = [p for p in candidates if p.get("outcome") == "resolved"
                    and p.get("evidence_event_id") == incident["resolution_event_id"]]
        if resolved:
            candidates = resolved
    if not active and candidates:
        latest_time = max(map(_plan_order, candidates))
        candidates = [p for p in candidates if _plan_order(p) == latest_time]
        # A sequence is causal evidence only when every contender has one. A
        # later unstarted cancellation has no sequence; ranking None below an
        # older started plan would invent an ordering just like ranking UUIDs.
        if len(candidates) > 1 and all(type(p.get("start_sequence")) is int for p in candidates):
            latest_sequence = max(p["start_sequence"] for p in candidates)
            candidates = [p for p in candidates if p["start_sequence"] == latest_sequence]
    candidates = sorted(candidates, key=lambda p: p["id"])
    return (candidates[0] if len(candidates) == 1 else None), candidates, len(candidates) > 1


def _observation(event_id, event):
    return {"event_id": event_id, "result": _text(event.get("result"), 80) or None,
            "narrative": _text(event.get("narrative"))}


def _assessment(event):
    return {"reason": _text(event.get("reason")),
            "source": _text(event.get("assessment_source"), 80) or "unknown",
            "goal_status": _text(event.get("goal_status"), 40) or "unknown"}


def _card_order(card):
    return (_STATE_PRIORITY[card["handling"]["state"]],
            _CATEGORY_PRIORITY.get(card["assessment"]["category"], 5),
            -card["updated_at"], card["id"])


def _incident_card(incident, plans, events, now, cooldown_seconds):
    related_plans = [p for p in plans if p["incident_id"] == incident["id"]]
    plan, candidates, ambiguous = _select_plan(incident, related_plans)
    original_ids = list(incident.get("evidence_ids", []))
    evidence_ids = list(dict.fromkeys(original_ids))
    resolution_id = incident.get("resolution_event_id")
    completed = [p for p in candidates if p["state"] == "completed"]
    result_ids = sorted({p["evidence_event_id"] for p in completed if p.get("evidence_event_id")})
    for event_id in ([resolution_id] if resolution_id else []) + result_ids:
        if event_id not in evidence_ids:
            evidence_ids.append(event_id)
    missing_count = sum(event_id not in events for event_id in evidence_ids)
    missing_count += sum(not p.get("evidence_event_id") for p in completed)
    missing_count += incident["status"] == "resolved" and not resolution_id
    missing_count += not original_ids
    latest_id = original_ids[-1] if original_ids else None
    latest = events.get(latest_id, {})
    result_id = plan.get("evidence_event_id") if plan and plan["state"] == "completed" else None
    result_event = events.get(result_id)
    last_unsuccessful = incident.get("last_unsuccessful_at")
    retry_after = (_time(last_unsuccessful) + cooldown_seconds
                   if last_unsuccessful is not None else None)
    cooling = retry_after is not None and now < retry_after
    outcome = (plan.get("outcome") if plan else None)
    if plan and plan["state"] == "cancelled":
        outcome = "cancelled"

    if missing_count:
        state = "evidence_missing"
        action = _action("inspect_evidence", "核对任务依据", incident, plan)
    elif ambiguous:
        state = "needs_reconciliation"
        action = _action("inspect_history", "核对历史方案与结果", incident)
    elif incident["status"] == "resolved":
        state = "resolved"
        action = _action("return_to_task", "返回原任务", incident, plan)
    elif incident.get("task_id") is None:
        state = "needs_task_link"
        action = _action("link_task", "关联原任务", incident)
    elif plan and plan["state"] == "started":
        if plan.get("incident_version") != incident["version"]:
            state = "needs_reconciliation"
            action = _action("reconcile_plan", "核对新增依据与原执行结果", incident, plan)
        else:
            state = "awaiting_result"
            action = _action("inspect_host_result", "查询原执行结果", incident, plan)
    elif plan and plan["state"] == "proposed":
        state = "awaiting_authorization"
        action = _action("review_proposal", "查看并确认方案", incident, plan)
    elif cooling:
        state = "cooldown"
        action = _action("review_previous_result", "核对上次结果", incident, plan)
    elif plan and plan["state"] == "completed" and outcome in ("failed", "partial"):
        state = "ready_for_retry"
        action = _action("prepare_repair", "准备新的核验方案", incident)
    elif plan and plan["state"] == "cancelled":
        state = "cancelled"
        action = _action("prepare_repair", "准备新的核验方案", incident)
    else:
        state = "needs_plan"
        action = _action("prepare_repair", "准备核验方案", incident)

    return {
        "id": incident["id"], "version": incident["version"],
        "task_id": incident.get("task_id"), "status": incident["status"],
        "title": _text(incident.get("title"), 160),
        "agents": list(incident.get("agents", [])),
        "original_task_id": incident.get("original_task_id"),
        "task_link_reason": _text(incident.get("task_link_reason")),
        "observation": _observation(latest_id, latest),
        "assessment": {
            "category": incident["category"],
            "reason": _text(latest.get("reason", incident.get("assessment_reason"))),
            "source": _text(latest.get("assessment_source", incident.get("assessment_source")), 80) or "unknown",
            "goal_status": _text(latest.get("goal_status"), 40) or "unknown",
        },
        "handling": {
            "state": state, "plan_id": plan["id"] if plan else None,
            "plan_ids": [p["id"] for p in candidates],
            "action": plan["action"] if plan else None if ambiguous else incident.get("recommended_action"),
            "last_outcome": outcome, "retry_after": retry_after,
            "cooldown_active": cooling,
            "evidence_event_id": result_id, "evidence_event_ids": result_ids,
            "observation": _observation(result_id, result_event) if result_event is not None else None,
            "assessment": _assessment(result_event) if result_event is not None else None,
        },
        "next_action": action,
        "evidence_ids": evidence_ids, "resolution_event_id": resolution_id,
        "missing_evidence_count": missing_count,
        "updated_at": max([_time(incident.get("updated_at"))]
                          + [_plan_order(p)[0] for p in related_plans]),
    }


def project_task(task_id, incidents, plans, events, now, cooldown_seconds):
    """Project one task; resolved incidents never imply host task completion."""
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("task_id must be a nonempty string")
    related = [i for i in incidents if i.get("task_id") == task_id]
    related_plans = [p for p in plans if p.get("task_id") == task_id]
    cards = sorted((_incident_card(i, related_plans, events, now, cooldown_seconds)
                    for i in related), key=_card_order)
    opened = [c for c in cards if c["status"] == "open"]
    resolved = [c for c in cards if c["status"] == "resolved"]
    active_count = sum(p["state"] in ("proposed", "started") for p in related_plans)
    event_ids = {event_id for event_id, event in events.items() if event.get("task_id") == task_id}
    for card in cards:
        event_ids.update(event_id for event_id in card["evidence_ids"] if event_id in events)
        if card["resolution_event_id"] in events:
            event_ids.add(card["resolution_event_id"])
    missing_count = sum(card["missing_evidence_count"] for card in cards)
    if missing_count or any(c["handling"]["state"] == "needs_reconciliation" for c in cards):
        status = "needs_attention"
    elif opened:
        status = "waiting" if all(c["handling"]["state"] in _WAITING for c in opened) else "needs_attention"
    elif active_count:
        status = "waiting"
    elif resolved:
        status = "resolved"
    elif event_ids:
        status = "observed"
    else:
        status = "no_evidence"
    primary = cards[0] if cards else None
    if primary:
        action = deepcopy(primary["next_action"])
    elif active_count:
        action = _action("inspect_host_result", "核对原方案与执行结果")
    elif event_ids:
        action = _action("inspect_task", "查看原任务")
    else:
        action = _action("record_evidence", "补充任务依据")
    return {
        "task_id": task_id, "recovery_status": status,
        "open_incident_count": len(opened), "resolved_incident_count": len(resolved),
        "observed_event_count": len(event_ids), "active_plan_count": active_count,
        "missing_evidence_count": missing_count,
        "primary_incident_id": primary["id"] if primary else None,
        "primary_action": action,
        "summary": primary["title"] if primary else ("核对原方案状态" if active_count
                                                    else "暂无已记录异常" if event_ids
                                                    else "尚无任务观察记录"),
        "updated_at": max([card["updated_at"] for card in cards]
                          + [_time(events[event_id].get("timestamp")) for event_id in event_ids], default=0.0),
        "incidents": cards,
    }


def project_queue(incidents, plans, events, now, cooldown_seconds):
    """Return compact unresolved-task rows and separate unlinked incident cards."""
    task_ids = {i["task_id"] for i in incidents if i.get("task_id") is not None}
    projected = [project_task(task_id, incidents, plans, events, now, cooldown_seconds) for task_id in task_ids]
    projected = [task for task in projected if task["recovery_status"] in ("needs_attention", "waiting")]
    projected.sort(key=lambda task: (_card_order(task["incidents"][0]), task["task_id"]))
    tasks = [{key: value for key, value in task.items() if key != "incidents"} for task in projected]
    unlinked = sorted((_incident_card(i, plans, events, now, cooldown_seconds)
                       for i in incidents if i["status"] == "open" and i.get("task_id") is None),
                      key=_card_order)
    return {"tasks": tasks, "unlinked_incidents": unlinked}
