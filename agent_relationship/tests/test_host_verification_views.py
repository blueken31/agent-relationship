"""Host receipts are visible without inventing assessments or resolution."""

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from agent_relationship import RelationshipTracker
from agent_relationship.llm.mock import MockLLM
from agent_relationship.state import export_state
from agent_relationship.task_views import project_task


class NoAssessmentCalls(MockLLM):
    def assess_interaction(self, *args, **kwargs):
        raise AssertionError("a read must not reassess")


class HostVerificationViewTests(unittest.TestCase):
    def start(self, storage_path=None):
        self.tracker = RelationshipTracker(storage_path=storage_path)
        self.addCleanup(self.tracker.close)
        issue = self.tracker.track("writer", "reviewer", {
            "event_id": "original", "task_id": "task", "action": "regression_check",
            "result": "failed", "narrative": "原回执缺失",
        }).incident_id
        self.issue = issue
        plan = self.tracker.propose_repair(issue, action="restricted_retest")
        self.plan = self.tracker.begin_repair(plan["id"], host_authorized=True)

    def result(self, event_id="result", agents=("writer", "reviewer"), **overrides):
        return self.tracker.track(*agents, {
            "event_id": event_id, "task_id": "task", "action": self.plan["action"],
            "repair_plan_id": self.plan["id"], "result": "success", "verification_passed": True,
            "narrative": "宿主复测通过", "private_payload": {"secret": "must-not-appear"},
            **overrides,
        })

    def original(self, view=None):
        return next(c for c in (view or self.tracker.task_view("task"))["incidents"]
                    if c["id"] == self.issue)

    def test_unknown_assessment_is_separate_from_verified_host_result(self):
        self.start()
        result = self.result()
        baseline = export_state(self.tracker)
        view = self.tracker.task_view("task")
        original = self.original(view)
        related = next(c for c in view["incidents"] if c["id"] != self.issue)
        self.assertEqual(view["summary"], "宿主复测已通过，协作评估待核对")
        self.assertEqual(view["primary_action"]["kind"], "inspect_assessment")
        self.assertEqual(original["handling"]["state"], "awaiting_assessment")
        self.assertEqual(original["observation"]["event_id"], "original")
        host = original["host_verification"]
        self.assertEqual(host["status"], "passed")
        self.assertEqual(host["event_id"], result.event_id)
        self.assertEqual(host["assessment"]["status"], "unknown")
        self.assertEqual(host["assessment"]["error_code"], "unsupported_mock_scenario")
        self.assertFalse(host["independently_verified"])
        self.assertFalse(host["grants_permission"])
        self.assertEqual(related["related_incident_id"], self.issue)
        self.assertEqual(related["host_verification"], host)
        self.assertEqual(original["evidence_ids"], ["original", "result"])
        self.assertEqual(view["open_incident_count"], 2)
        self.assertEqual(view["resolved_incident_count"], 0)
        self.assertEqual(self.tracker.task_queue()["tasks"][0]["primary_action"], view["primary_action"])
        self.assertNotIn("must-not-appear", str(view))
        self.assertNotIn("private_payload", str(view))
        with self.assertRaisesRegex(ValueError, "verified assessment"):
            self.tracker.complete_repair(self.plan["id"], "resolved", result.event_id)
        self.assertEqual(export_state(self.tracker), baseline)
        host["event_ids"].clear()
        self.assertEqual(self.original()["host_verification"]["event_ids"], ["result"])

    def test_success_text_and_non_boolean_flags_do_not_become_verified(self):
        for flag in (None, False, 1, "true"):
            with self.subTest(flag=flag):
                self.start()
                self.result(verification_passed=flag)
                original = self.original()
                self.assertEqual(original["host_verification"]["status"], "reported")
                self.assertEqual(original["handling"]["state"], "result_recorded")
                self.assertEqual(original["next_action"]["kind"], "review_host_result")
                self.tracker.close()

    def test_wrong_task_pair_action_and_plan_never_attach_to_original(self):
        for override in ({"task_id": "other"}, {"agents": ("writer", "other")},
                         {"action": "other"}, {"repair_plan_id": "other"}):
            with self.subTest(override=override):
                self.start()
                self.result(**override)
                self.assertIsNone(self.original()["host_verification"])
                self.assertEqual(self.original()["handling"]["state"], "awaiting_result")
                self.tracker.close()

    def test_lifecycle_and_missing_outcome_do_not_become_received_results(self):
        for outcome in (None, {}, "", "started", "running", "queued"):
            with self.subTest(outcome=outcome):
                self.start()
                self.result(result=outcome, verification_passed=False)
                self.assertIsNone(self.original()["host_verification"])
                self.assertEqual(self.original()["handling"]["state"], "awaiting_result")
                self.tracker.close()

    def test_sequence_and_timestamp_both_bound_the_result_after_start(self):
        self.start()
        self.result()
        inputs = self.tracker._task_projection_inputs("task")
        for override in ({"sequence": None}, {"sequence": True},
                         {"sequence": self.plan["start_sequence"]},
                         {"timestamp": self.plan["started_at"] - 1}):
            with self.subTest(override=override):
                incidents, plans, events = deepcopy(inputs)
                events["result"].update(override)
                view = project_task("task", incidents, plans, events, now=10**12, cooldown_seconds=3600)
                self.assertIsNone(self.original(view)["host_verification"])
        incidents, plans, events = deepcopy(inputs)
        # Equal wall-clock times remain distinguishable by stored ingestion order.
        events["result"]["timestamp"] = self.plan["started_at"]
        view = project_task("task", incidents, plans, events, now=10**12, cooldown_seconds=3600)
        self.assertEqual(self.original(view)["host_verification"]["status"], "passed")

    def test_competing_receipts_require_reconciliation_without_selecting_latest(self):
        self.start()
        self.result("z-result")
        self.result("a-result", verification_passed=False, result="partial")
        view = self.tracker.task_view("task")
        original = self.original(view)
        host = original["host_verification"]
        self.assertEqual(host["status"], "multiple")
        self.assertIsNone(host["event_id"])
        self.assertIsNone(host["assessment"])
        self.assertEqual(host["event_ids"], ["z-result", "a-result"])
        self.assertEqual(view["primary_action"]["kind"], "inspect_host_results")
        self.assertEqual(original["handling"]["state"], "needs_reconciliation")
        incidents, plans, events = self.tracker._task_projection_inputs("task")
        reordered = project_task("task", incidents, plans, dict(reversed(list(events.items()))),
                                 now=10**12, cooldown_seconds=3600)
        self.assertEqual(reordered, view)

    def test_new_failure_keeps_passed_receipt_but_overrides_assessment_only_advice(self):
        self.start()
        self.result()
        self.tracker.track("writer", "reviewer", {
            "event_id": "new-failure", "task_id": "task", "action": "regression_check", "result": "failed",
        })
        view = self.tracker.task_view("task")
        self.assertEqual(self.original(view)["host_verification"]["status"], "passed")
        self.assertEqual(self.original(view)["handling"]["state"], "needs_reconciliation")
        self.assertEqual(view["primary_action"]["kind"], "reconcile_plan")
        self.assertNotEqual(view["summary"], "宿主复测已通过，协作评估待核对")

    def test_unrelated_unknown_evidence_is_not_grouped_with_the_passed_result(self):
        self.start()
        self.result()
        self.tracker.track("writer", "reviewer", {
            "event_id": "unrelated", "task_id": "task", "action": "unknown", "result": "success",
        })
        view = self.tracker.task_view("task")
        evidence_issue = next(c for c in view["incidents"] if c["id"] != self.issue)
        self.assertIsNone(evidence_issue["related_incident_id"])
        self.assertIsNone(evidence_issue["host_verification"])
        self.assertNotEqual(view["summary"], "宿主复测已通过，协作评估待核对")

    def test_model_goal_violation_remains_visible_even_when_host_reports_pass(self):
        self.start()
        self.result(scenario="goal_violation")
        view = self.tracker.task_view("task")
        self.assertEqual(self.original(view)["host_verification"]["status"], "passed")
        self.assertEqual(self.original(view)["host_verification"]["assessment"]["goal_status"], "violated")
        self.assertEqual(view["incidents"][0]["assessment"]["category"], "goal_violation")
        self.assertNotEqual(view["primary_action"]["kind"], "inspect_assessment")
        self.assertEqual(view["resolved_incident_count"], 0)

    def test_cancelled_plan_receipt_does_not_reactivate_its_handling_state(self):
        self.start()
        self.tracker.cancel_repair(self.plan["id"])
        self.result()
        card = self.original()
        self.assertEqual(card["handling"]["state"], "cooldown")
        self.assertEqual(card["host_verification"]["status"], "passed")
        self.assertEqual(self.tracker.repair_plan(self.plan["id"])["state"], "cancelled")

    def test_restart_and_reads_preserve_unknown_assessments_and_exact_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tracker.sqlite3"
            self.start(path)
            self.result()
            view = self.tracker.task_view("task")
            baseline = export_state(self.tracker)
            sequence = self.tracker.event("result")["sequence"]
            self.tracker.close()
            with RelationshipTracker(llm=NoAssessmentCalls(), storage_path=path) as reopened:
                self.assertEqual(reopened.task_view("task"), view)
                self.assertEqual(reopened.event("result")["sequence"], sequence)
                reopened.task_queue()
                self.assertEqual(export_state(reopened), baseline)

    def test_missing_result_keeps_missing_evidence_priority(self):
        self.start()
        self.result()
        del self.tracker.engine.event_records["result"]
        view = self.tracker.task_view("task")
        self.assertEqual(view["primary_action"]["kind"], "inspect_evidence")
        self.assertEqual(view["incidents"][0]["handling"]["state"], "evidence_missing")
        self.assertIsNone(self.original(view)["host_verification"])
