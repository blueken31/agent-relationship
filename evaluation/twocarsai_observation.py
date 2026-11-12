"""Bounded PR #238 observation probe; no inference, executor or quality score.

The caller runs the original 2CarsAI tests and supplies redacted receipts. The
two identities below are analysis roles, not captured runtime agent identities.
An unavailable assessor stays unknown, including after a passing host test.
"""

from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re

from agent_relationship import RelationshipTracker
from agent_relationship.llm.base import LLMBackend


BEFORE = "78f04e2352f6124daa0b83924bbe989d2e42d7ef"
FIXED = "91b814fbed164c5781d7cbd58e8d7e3296e13f00"
TEST = "test_resumed_dealer_task_keeps_original_contact_order_and_memos"
PAIR = ("analysis-role:implementation", "analysis-role:review")
NAMES = [TEST + "[False]", TEST + "[True]"]


class UnavailableAssessor(LLMBackend):
    """No provider is configured. 'model' denotes the unavailable backend type."""

    def chat(self, *args, **kwargs):
        raise AssertionError("This observation probe must not call a provider")

    def assess_interaction(self, *args, **kwargs):
        return self._unknown("assessment_not_configured")


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False).encode()).hexdigest()


def validate_receipt(receipt, revision, status):
    """Fail on missing/skipped/setup-error evidence instead of calling it repair.

    Digests and declared source revisions support auditing; they do not attest
    that a caller executed a command. This specific probe accepts exactly the
    two original regression cases, with the same failure observed in #238.
    """
    if receipt.get("source_revision") != revision:
        raise ValueError("unexpected source revision")
    for field in ("junit_sha256", "test_file_sha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", receipt.get(field, "")):
            raise ValueError("missing content digest")
    timestamps = []
    for field in ("started_at", "finished_at"):
        value = datetime.fromisoformat(receipt[field])
        if value.tzinfo is None:
            raise ValueError("receipt timestamps require a timezone")
        timestamps.append(value.timestamp())
    if timestamps[0] > timestamps[1]:
        raise ValueError("receipt time order is invalid")
    expected_exit = 1 if status == "failed" else 0
    if type(receipt.get("exit_code")) is not int or receipt["exit_code"] != expected_exit:
        raise ValueError("unexpected process outcome")
    cases = receipt.get("cases", [])
    if sorted(case["name"] for case in cases) != sorted(NAMES):
        raise ValueError("expected exactly the two original cases")
    if any(case["status"] != status for case in cases):
        raise ValueError("skipped, errored or unexpected regression outcome")
    if status == "failed" and any(
        case.get("failure_message", "").split("\n", 1)[0]
        != "AssertionError: assert [] == ['订单已确认']" for case in cases
    ):
        raise ValueError("failure does not reproduce the reviewed defect")


def _context(receipt, index, result):
    name = NAMES[index]
    return {
        "event_id": "pr238:" + receipt["junit_sha256"] + ":" + str(index),
        "task_id": "2carsai-pr238:saved-cluster=" + str(bool(index)).lower(),
        "action": "regression_check",
        "goal": "Retain the original contact and buyer receipts after saving a purchase task",
        "expected_result": "Original confirmation/refund receipts remain visible; unrelated contacts and unauthorized readers are excluded",
        "result": result,
        "narrative": ("Original regression fails: contract confirmed but its memo is missing."
                      if result == "failed" else
                      "The same original HTTP/PostgreSQL regression passes on the repair commit."),
        "evidence_refs": ["sha256:" + receipt["junit_sha256"] + "#" + name],
        "provenance": {
            "source": "2carsai_original_pytest", "source_revision": receipt["source_revision"],
            "receipt_sha256": _digest(receipt), "host_started_at": receipt["started_at"],
            "host_finished_at": receipt["finished_at"],
            "identities": "analysis_roles_not_captured_agent_ids",
        },
    }


def observe(before, verify, storage_path):
    """Record failures, reopen SQLite, then request one fresh bounded host check.

    ``verify`` is a caller-owned callback returning the repaired test receipt.
    The SDK never executes the tests, grants permission, or supplies a verdict.
    Both parameter variants share the one host invocation. Each SDK plan covers
    only its original sample. Existing state is refused to avoid accidental reruns.
    """
    validate_receipt(before, BEFORE, "failed")
    path = Path(storage_path)
    # Exclusive creation prevents a caller from silently reusing a previous run.
    with path.open("xb"):
        pass
    events = [_context(before, i, "failed") for i in range(2)]
    incident_ids, before_views = [], []
    with RelationshipTracker(llm=UnavailableAssessor(), storage_path=path) as tracker:
        for event in events:
            recorded = tracker.track(*PAIR, event)
            duplicate = tracker.track(*PAIR, event)
            if not duplicate.duplicate or duplicate.incident_id != recorded.incident_id:
                raise RuntimeError("identical evidence was not deduplicated")
            incident_ids.append(recorded.incident_id)
            before_views.append(tracker.task_view(event["task_id"]))

    plans, stale_rejections = [], []
    with RelationshipTracker(llm=UnavailableAssessor(), storage_path=path) as tracker:
        reopened = [tracker.task_view(event["task_id"]) for event in events]
        if reopened != before_views:
            raise RuntimeError("task evidence changed when reopening SQLite")
        for incident, event in zip(incident_ids, events):
            plan = tracker.propose_repair(incident, action="restricted_retest")
            plan = tracker.begin_repair(plan["id"], host_authorized=True)
            plans.append(plan)
            try:
                tracker.complete_repair(plan["id"], "resolved", event["event_id"])
            except ValueError as exc:
                stale_rejections.append(str(exc))
            else:
                raise RuntimeError("old failing evidence unexpectedly closed a repair")

    fixed = verify()
    validate_receipt(fixed, FIXED, "passed")
    if fixed["test_file_sha256"] != before["test_file_sha256"]:
        raise ValueError("the two revisions did not run the same test file")
    if datetime.fromisoformat(fixed["started_at"]).timestamp() <= max(p["started_at"] for p in plans):
        raise ValueError("host verification predates the recovery plan")

    closure_rejections, after_views, wrong_task_rejections = [], [], []
    with RelationshipTracker(llm=UnavailableAssessor(), storage_path=path) as tracker:
        for i, plan in enumerate(plans):
            event = _context(fixed, i, "success")
            event.update(action="restricted_retest", repair_plan_id=plan["id"],
                         verification_passed=True)
            record = tracker.track(*PAIR, event)
            try:
                tracker.complete_repair(plans[1-i]["id"], "resolved", record.event_id)
            except ValueError as exc:
                wrong_task_rejections.append(str(exc))
            else:
                raise RuntimeError("foreign-task evidence unexpectedly closed a repair")
            try:
                tracker.complete_repair(plan["id"], "resolved", record.event_id)
            except ValueError as exc:
                closure_rejections.append(str(exc))
            else:
                raise RuntimeError("unknown assessment unexpectedly resolved the SDK incident")
            after_views.append(tracker.task_view(plan["task_id"]))
    return {
        "mode": "project_receipt_observation",
        "receipts_are_operator_supplied": True,
        "native_vs_sdk_experiment": False,
        "runtime_multiagent_trace_captured": False,
        "identities": "analysis_roles_not_captured_agent_ids",
        "sdk_provider_calls": 0,
        "usage": {"input_tokens": None, "output_tokens": None,
                  "cost": None, "currency": None},
        "checks": {"duplicate_evidence_deduplicated": True,
                   "sqlite_reopen_preserves_tasks": True,
                   "old_failure_rejected": len(stale_rejections) == 2,
                   "foreign_task_rejected": len(wrong_task_rejections) == 2,
                   "fresh_host_verification_after_plans": True,
                   "host_regression_recovered": True,
                   "sdk_incidents_resolved": False},
        "old_failure_rejections": stale_rejections,
        "foreign_task_rejections": wrong_task_rejections,
        "closure_rejections": closure_rejections,
        "before_views": before_views, "after_views": after_views,
        "plans": plans, "before": deepcopy(before), "fixed": deepcopy(fixed),
    }
