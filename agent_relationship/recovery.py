"""Task-scoped incident evidence and bounded recovery bookkeeping.

This in-memory ledger never executes actions or grants permissions. The host
authorizes and runs a proposed action, records its result, and then submits that
record as evidence. Relationship scores cannot close an incident.
"""

from copy import deepcopy
from dataclasses import asdict
import math
from threading import RLock
import time
from typing import Callable, Dict, List, Optional, Tuple
from uuid import uuid4

from .models import InteractionRecord
from .evidence_policy import (LEGACY, TASK_EVIDENCE, validate_policy, validate_context,
                              condition_scope, validate_contract, bind_contract,
                              host_result_matches, adverse_since_start)


class RecoveryLedger:
    """Keep explicit issues open until a task-bound recovery is verified.

    ``observe`` must receive every recorded event, including recovery results.
    Returned dictionaries are detached snapshots. ``host_authorized=True`` is
    the host's declaration, not a permission issued by this library.
    """

    COOLDOWN_SECONDS = 3600
    ACTIONS = {
        "verify_handoff": {
            "description": "核对交接资料、版本与验收要求",
            "scope": "仅核对原任务资料与交接要求，不执行原任务的外部写入",
            "success_criteria": "原任务交接缺项已补齐，资料版本与验收要求已核对",
        },
        "restricted_retest": {
            "description": "限定一个样本复测原任务的失败步骤",
            "scope": "仅使用宿主批准的单个样本复测，不扩大原任务权限或预算",
            "success_criteria": "宿主核验该样本满足原失败步骤的验收要求",
        },
        "reassign_task": {
            "description": "由宿主核验替代执行者并接续原任务",
            "scope": "宿主核验替代执行者的权限、能力和上下文后，仅交接原任务",
            "success_criteria": "宿主确认原任务已由合格替代执行者接续且原问题解除",
        },
    }

    def __init__(self, clock: Callable[[], float] = time.time, diagnosis_policy: str = LEGACY):
        if not callable(clock):
            raise ValueError("clock must be callable")
        validate_policy(diagnosis_policy)
        self.diagnosis_policy = diagnosis_policy
        self._clock = clock
        self._last_clock: Optional[float] = None
        self._lock = RLock()
        self._incidents: Dict[str, Dict] = {}
        self._plans: Dict[str, Dict] = {}
        self._events: Dict[str, Dict] = {}
        self._event_incidents: Dict[str, Optional[str]] = {}
        self._event_order: Dict[str, int] = {}
        self._sequence = 0
        self._now()

    def _now(self) -> float:
        value = self._clock()
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("clock must return a finite nonnegative number")
        if not math.isfinite(value) or value < 0:
            raise ValueError("clock must return a finite nonnegative number")
        if self._last_clock is not None and value < self._last_clock:
            raise ValueError("clock moved backwards")
        self._last_clock = float(value)
        return float(value)

    @staticmethod
    def _pair(a: str, b: str) -> Tuple[str, str]:
        return tuple(sorted((a, b)))

    @staticmethod
    def _snapshot(record: InteractionRecord) -> Dict:
        if not isinstance(record, InteractionRecord):
            raise ValueError("evidence must be an InteractionRecord")
        if not isinstance(record.event_id, str) or not record.event_id.strip():
            raise ValueError("event_id is required")
        if record.task_id is not None and (
            not isinstance(record.task_id, str) or not record.task_id.strip()
        ):
            raise ValueError("task_id must be nonempty when provided")
        if (
            isinstance(record.timestamp, bool)
            or not isinstance(record.timestamp, (int, float))
            or not math.isfinite(record.timestamp)
            or record.timestamp < 0
        ):
            raise ValueError("event timestamp must be finite and nonnegative")
        return deepcopy(asdict(record))

    def _category(self, record: InteractionRecord, risk: str) -> Optional[str]:
        evidence_policy = self.diagnosis_policy == TASK_EVIDENCE
        if record.goal_status == "violated" and (record.is_assessed or evidence_policy):
            return "goal_violation"
        impacts = [v for v in (record.impact_a, record.impact_b) if v is not None]
        if record.assessment_status == "assessed" and impacts and min(impacts) <= -0.5:
            return "material_harm"
        if record.context.get("result") in ("failed", "error", "timeout"):
            return "task_failure"
        if evidence_policy and record.context.get("evidence_requirement"):
            return "evidence_needed"
        if not evidence_policy and record.assessment_status != "assessed":
            return "evidence_needed"
        # A good event can arrive while the aggregate score is still critical.
        # It is not fresh adverse evidence and must not invalidate its own repair.
        if risk == "critical" and impacts and min(impacts) < 0:
            return "collaboration_risk"
        return None

    def observe(self, record: InteractionRecord, risk: str) -> Optional[str]:
        """Record evidence and create/extend an open issue when warranted."""
        with self._lock:
            snapshot = self._snapshot(record)
            if self.diagnosis_policy == TASK_EVIDENCE:
                validate_context(record.context)
            if record.event_id in self._events:
                if snapshot != self._events[record.event_id]:
                    raise ValueError("event_id already belongs to different evidence")
                return self._event_incidents[record.event_id]
            now = self._now()
            if record.timestamp > now:
                raise ValueError("event timestamp is in the future")
            category = self._category(record, risk)
            pair = self._pair(record.initiator, record.target)
            incident = None
            if category is not None:
                incident = next((i for i in self._incidents.values() if (
                    i["status"] == "open" and tuple(i["agents"]) == pair
                    and i["task_id"] == record.task_id and i["category"] == category
                    and (self.diagnosis_policy == LEGACY or condition_scope(record.context, category)
                         == condition_scope(self._events[i["evidence_ids"][0]]["context"], category))
                )), None)
                if incident is None:
                    titles = {
                        "goal_violation": "任务目标可能被违反，需核验",
                        "material_harm": "发现显著不利影响，需核验",
                        "task_failure": "原任务步骤未完成",
                        "evidence_needed": "评估依据不足，等待核验",
                        "collaboration_risk": "协作出现异常，需核验",
                    }
                    if self.diagnosis_policy == TASK_EVIDENCE:
                        titles["evidence_needed"] = "当前动作缺少必需证据"
                    incident = {
                        "id": "incident_" + uuid4().hex,
                        "agents": list(pair),
                        "initiator": record.initiator,
                        "target": record.target,
                        "task_id": record.task_id,
                        "original_task_id": record.task_id,
                        "task_link_reason": None,
                        "task_linked_at": None,
                        "category": category,
                        "title": titles[category],
                        "reason": record.context.get("narrative", ""),
                        "assessment_reason": record.assessment_reason,
                        "assessment_source": record.assessment_source,
                        "evidence_ids": [],
                        "status": "open",
                        "recommended_action": ("restricted_retest" if self.diagnosis_policy == TASK_EVIDENCE
                                               and category == "task_failure" else "verify_handoff"),
                        "created_at": now,
                        "updated_at": now,
                        "version": 0,
                        "resolution_event_id": None,
                        "last_unsuccessful_at": None,
                    }
                    self._incidents[incident["id"]] = incident
                incident["evidence_ids"].append(record.event_id)
                incident["version"] += 1
                incident["updated_at"] = now
            self._sequence += 1
            self._events[record.event_id] = snapshot
            self._event_order[record.event_id] = self._sequence
            incident_id = incident["id"] if incident else None
            self._event_incidents[record.event_id] = incident_id
            return incident_id

    def list_incidents(
        self, task_id: Optional[str] = None, status: Optional[str] = None,
    ) -> List[Dict]:
        if status not in (None, "open", "resolved"):
            raise ValueError("status must be open or resolved")
        with self._lock:
            return deepcopy([
                i for i in self._incidents.values()
                if (task_id is None or i["task_id"] == task_id)
                and (status is None or i["status"] == status)
            ])

    def open_for_pair(
        self, a: str, b: str, task_id: Optional[str] = None,
    ) -> List[Dict]:
        with self._lock:
            pair = self._pair(a, b)
            return deepcopy([
                i for i in self._incidents.values()
                if i["status"] == "open" and tuple(i["agents"]) == pair
                and (task_id is None or i["task_id"] == task_id)
            ])

    def list_plans(self, task_id: Optional[str] = None, incident_id: Optional[str] = None) -> List[Dict]:
        """Read existing plans without creating one or advancing the clock."""
        for value in (task_id, incident_id):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError("plan filters must be nonempty strings")
        with self._lock:
            return deepcopy([
                plan for plan in self._plans.values()
                if (task_id is None or plan["task_id"] == task_id)
                and (incident_id is None or plan["incident_id"] == incident_id)
            ])

    def get_plan(self, plan_id: str) -> Optional[Dict]:
        """Return a detached existing plan, or None for an unknown ID."""
        if not isinstance(plan_id, str) or not plan_id.strip():
            raise ValueError("plan_id must be a nonempty string")
        with self._lock:
            return deepcopy(self._plans.get(plan_id))

    def link_incident(self, incident_id: str, task_id: str, reason: str) -> Dict:
        """Record a host-supplied task correction for an unscoped open issue.

        The original event snapshots remain unchanged. This explicit link only
        scopes future recovery; it does not infer task identity or rewrite past
        evidence. Already scoped issues cannot be moved to another task.
        """
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("a nonempty task_id is required")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("a nonempty task-link reason is required")
        with self._lock:
            incident = self._incidents.get(incident_id)
            if not incident or incident["status"] != "open":
                raise ValueError("an open incident is required")
            if incident["task_id"] is not None:
                if (
                    incident["original_task_id"] is None
                    and incident["task_id"] == task_id
                    and incident["task_link_reason"] == reason
                ):
                    return deepcopy(incident)
                raise ValueError("an already scoped incident cannot be rebound")
            if any(
                p["incident_id"] == incident_id
                and p["state"] not in ("completed", "cancelled")
                for p in self._plans.values()
            ):
                raise ValueError("an active recovery plan prevents task linking")
            now = self._now()
            incident.update({
                "task_id": task_id,
                "task_link_reason": reason,
                "task_linked_at": now,
                "updated_at": now,
                "version": incident["version"] + 1,
            })
            return deepcopy(incident)

    def propose_repair(self, incident_id: str, action: str = "verify_handoff", *,
                       verification_contract: Optional[Dict] = None) -> Dict:
        """Return a single bounded proposal; this does not authorize execution."""
        with self._lock:
            if action not in self.ACTIONS:
                raise ValueError("unsupported recovery action")
            incident = self._incidents.get(incident_id)
            if not incident or incident["status"] != "open":
                raise ValueError("an open incident is required")
            if not incident["task_id"]:
                raise ValueError("task_id is required before proposing recovery")
            contract = None
            if verification_contract is not None:
                if self.diagnosis_policy != TASK_EVIDENCE:
                    raise ValueError("verification contracts require task_evidence_v1")
                contract = validate_contract(verification_contract)
                bind_contract(contract, incident, self._events, action)
            active = next((p for p in self._plans.values() if (
                p["incident_id"] == incident_id
                and p["state"] not in ("completed", "cancelled")
            )), None)
            if active:
                if active["action"] != action or active.get("verification_contract") != contract:
                    raise ValueError("an active plan already has a different action or verification contract")
                return deepcopy(active)
            now = self._now()
            last = incident["last_unsuccessful_at"]
            if last is not None and now - last < self.COOLDOWN_SECONDS:
                raise ValueError("recovery cooldown is active")
            plan = {
                "id": "repair_" + uuid4().hex,
                "incident_id": incident_id,
                "task_id": incident["task_id"],
                "agents": list(incident["agents"]),
                "action": action,
                **self.ACTIONS[action],
                "max_attempts": 1,
                "requires_host_authorization": True,
                "grants_permission": False,
                "state": "proposed",
                "created_at": now,
                "started_at": None,
                "completed_at": None,
                "cancelled_at": None,
                "outcome": None,
                "evidence_event_id": None,
                "incident_version": None,
                "start_sequence": None,
            }
            if self.diagnosis_policy == TASK_EVIDENCE:
                plan["verification_contract"] = contract
                plan["proposal_sequence"] = self._sequence
                plan["completion_sequence"] = None
            self._plans[plan["id"]] = plan
            return deepcopy(plan)

    def begin_repair(self, plan_id: str, host_authorized: bool = False) -> Dict:
        """Record the host's explicit authorization for one execution attempt."""
        with self._lock:
            if host_authorized is not True:
                raise ValueError("the host must explicitly authorize this action")
            plan = self._plans.get(plan_id)
            if not plan or plan["state"] != "proposed":
                raise ValueError("only an unstarted plan can begin")
            incident = self._incidents[plan["incident_id"]]
            if incident["status"] != "open":
                raise ValueError("incident is no longer open")
            if plan.get("verification_contract") is not None:
                bind_contract(plan["verification_contract"], incident, self._events, plan["action"])
            now = self._now()
            plan.update({
                "state": "started", "started_at": now,
                "incident_version": incident["version"],
                "start_sequence": self._sequence,
            })
            return deepcopy(plan)

    def cancel_repair(self, plan_id: str) -> Dict:
        """Cancel bookkeeping without closing the issue or undoing host work.

        A started attempt remains consumed and enters the same cooldown as an
        unsuccessful attempt. A proposal that never started can be replaced
        immediately. The host must separately stop any execution in progress.
        """
        with self._lock:
            plan = self._plans.get(plan_id)
            if not plan:
                raise ValueError("unknown recovery plan")
            if plan["state"] == "cancelled":
                return deepcopy(plan)
            if plan["state"] == "completed":
                raise ValueError("a completed recovery cannot be cancelled")
            now = self._now()
            if plan["state"] == "started":
                incident = self._incidents[plan["incident_id"]]
                incident["last_unsuccessful_at"] = now
                incident["updated_at"] = now
            plan.update({"state": "cancelled", "cancelled_at": now})
            return deepcopy(plan)

    def complete_repair(
        self, plan_id: str, outcome: str, evidence: InteractionRecord,
    ) -> Dict:
        """Attach a recorded result; verified resolution closes only its issue."""
        with self._lock:
            if outcome not in ("resolved", "failed", "partial"):
                raise ValueError("outcome must be resolved, failed or partial")
            plan = self._plans.get(plan_id)
            if not plan:
                raise ValueError("unknown recovery plan")
            snapshot = self._snapshot(evidence)
            if self._events.get(evidence.event_id) != snapshot:
                raise ValueError("evidence must match a previously observed event")
            if plan["state"] == "completed":
                if plan["outcome"] == outcome and plan["evidence_event_id"] == evidence.event_id:
                    return deepcopy(plan)
                raise ValueError("recovery result is already recorded")
            if plan["state"] != "started":
                raise ValueError("recovery plan has not started")
            if evidence.task_id != plan["task_id"] or list(self._pair(
                evidence.initiator, evidence.target,
            )) != plan["agents"]:
                raise ValueError("evidence must belong to the original task and agents")
            if (
                evidence.interaction_type != plan["action"]
                or evidence.context.get("repair_plan_id") != plan_id
            ):
                raise ValueError("evidence must identify the planned recovery action")
            if (
                evidence.timestamp < plan["started_at"]
                or self._event_order[evidence.event_id] <= plan["start_sequence"]
            ):
                raise ValueError("recovery needs new evidence recorded after it started")
            incident = self._incidents[plan["incident_id"]]
            if outcome == "resolved":
                if incident["version"] != plan["incident_version"]:
                    raise ValueError("new adverse evidence requires a fresh recovery assessment")
                contract = plan.get("verification_contract")
                if contract is not None:
                    bind_contract(contract, incident, self._events, plan["action"])
                    if adverse_since_start(plan, self._incidents, self._event_order, self._sequence):
                        raise ValueError("new adverse evidence requires a fresh recovery assessment")
                    if not host_result_matches(plan, evidence.context, evidence.timestamp, evidence.goal_status):
                        raise ValueError("resolution requires a fresh matching host verification receipt")
                elif not (
                    evidence.context.get("verification_passed") is True
                    and evidence.context.get("result") == "success"
                    and evidence.assessment_status == "assessed"
                    and evidence.goal_status != "violated"
                    and (
                        incident["category"] != "goal_violation"
                        or evidence.goal_status == "met"
                    )
                    and evidence.impact_a is not None and evidence.impact_a >= 0
                    and evidence.impact_b is not None and evidence.impact_b >= 0
                ):
                    raise ValueError("resolution requires a successful, verified assessment")
            now = self._now()
            plan.update({
                "state": "completed", "completed_at": now,
                "outcome": outcome, "evidence_event_id": evidence.event_id,
            })
            if self.diagnosis_policy == TASK_EVIDENCE:
                plan["completion_sequence"] = self._sequence
            incident["updated_at"] = now
            if outcome == "resolved":
                incident["status"] = "resolved"
                incident["resolution_event_id"] = evidence.event_id
            else:
                incident["last_unsuccessful_at"] = now
            return deepcopy(plan)
