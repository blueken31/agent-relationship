"""Recovery must be justified by the original task's recorded evidence."""

from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import unittest

from agent_relationship.models import InteractionRecord
from agent_relationship.recovery import RecoveryLedger


class Clock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value

    def advance(self, seconds=1):
        self.value += seconds


class TestRecoveryLedger(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.ledger = RecoveryLedger(clock=self.clock)
        self.counter = 0

    def event(self, **overrides):
        self.counter += 1
        values = {
            "timestamp": self.clock(), "event_id": f"event-{self.counter}",
            "interaction_type": "handoff", "initiator": "writer",
            "target": "reviewer", "task_id": "report-1",
            "impact_a": -0.6, "impact_b": -0.6,
            "context": {"result": "failed", "narrative": "资料版本不同"},
            "assessment_reason": "交接失败", "assessment_source": "mock",
            "assessment_status": "assessed", "goal_status": "unknown",
        }
        values.update(overrides)
        return InteractionRecord(**values)

    def started(self, **event_overrides):
        original = self.event(**event_overrides)
        incident_id = self.ledger.observe(original, "critical")
        plan = self.ledger.propose_repair(incident_id)
        self.clock.advance()
        plan = self.ledger.begin_repair(plan["id"], host_authorized=True)
        self.clock.advance()
        return original, incident_id, plan

    def result(self, plan, **overrides):
        values = {
            "interaction_type": plan["action"], "impact_a": 0.2, "impact_b": 0.2,
            "goal_status": "met", "context": {
                "repair_plan_id": plan["id"], "verification_passed": True,
                "result": "success", "narrative": "资料版本与验收要求已核对",
            },
        }
        values.update(overrides)
        return self.event(**values)

    def test_first_serious_event_creates_actionable_task_issue(self):
        record = self.event()
        incident_id = self.ledger.observe(record, "critical")
        issue = self.ledger.list_incidents()[0]
        self.assertEqual(issue["id"], incident_id)
        self.assertEqual(issue["task_id"], "report-1")
        self.assertEqual(issue["reason"], "资料版本不同")
        self.assertEqual(issue["assessment_source"], "mock")
        self.assertEqual(issue["evidence_ids"], [record.event_id])
        self.assertEqual(issue["recommended_action"], "verify_handoff")

    def test_good_events_cannot_wash_away_open_incident(self):
        incident_id = self.ledger.observe(self.event(), "critical")
        for _ in range(20):
            positive = self.event(
                impact_a=0.3, impact_b=0.3, context={"result": "success"},
            )
            self.assertIsNone(self.ledger.observe(positive, "critical"))
        issues = self.ledger.list_incidents(status="open")
        self.assertEqual([i["id"] for i in issues], [incident_id])
        self.assertEqual(len(issues[0]["evidence_ids"]), 1)

    def test_grouping_is_task_scoped_and_deduplicates_event(self):
        first = self.event()
        incident_id = self.ledger.observe(first, "critical")
        self.assertEqual(self.ledger.observe(first, "critical"), incident_id)
        self.assertEqual(self.ledger.observe(self.event(), "critical"), incident_id)
        other = self.ledger.observe(self.event(task_id="report-2"), "critical")
        self.assertNotEqual(incident_id, other)
        self.assertEqual(len(self.ledger.list_incidents()), 2)
        self.assertEqual(len(self.ledger.open_for_pair("reviewer", "writer")), 2)
        self.assertEqual(len(self.ledger.open_for_pair("writer", "reviewer", "report-1")), 1)
        self.assertEqual(len(self.ledger.list_incidents(task_id="report-1")[0]["evidence_ids"]), 2)

    def test_reusing_event_id_with_different_payload_is_rejected(self):
        record = self.event()
        self.ledger.observe(record, "critical")
        with self.assertRaisesRegex(ValueError, "different evidence"):
            self.ledger.observe(replace(record, task_id="report-2"), "critical")

    def test_unknown_is_evidence_needed_not_malicious(self):
        record = self.event(
            assessment_status="unknown", impact_a=None, impact_b=None,
            context={"result": "success"},
        )
        self.ledger.observe(record, "unknown")
        self.assertEqual(self.ledger.list_incidents()[0]["category"], "evidence_needed")

    def test_goal_violation_and_actual_failure_raise_issues_despite_positive_score(self):
        record = self.event(
            goal_status="violated", impact_a=0.5, impact_b=0.5,
            context={"result": "success"},
        )
        self.ledger.observe(record, "low")
        failure = self.event(impact_a=-0.1, impact_b=-0.1, task_id="report-2")
        self.ledger.observe(failure, "low")
        categories = {i["category"] for i in self.ledger.list_incidents()}
        self.assertEqual(categories, {"goal_violation", "task_failure"})

    def test_plans_require_task_and_do_not_grant_authority(self):
        issue = self.ledger.observe(self.event(task_id=None), "critical")
        with self.assertRaisesRegex(ValueError, "task_id"):
            self.ledger.propose_repair(issue)
        issue = self.ledger.observe(self.event(), "critical")
        plan = self.ledger.propose_repair(issue)
        self.assertEqual(plan["max_attempts"], 1)
        self.assertFalse(plan["grants_permission"])
        self.assertTrue(plan["requires_host_authorization"])
        self.assertEqual(self.ledger.propose_repair(issue), plan)
        with self.assertRaises(ValueError):
            self.ledger.propose_repair(issue, "restricted_retest")
        for value in (False, None, 1, "true"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.ledger.begin_repair(plan["id"], host_authorized=value)

    def test_explicit_task_link_enables_recovery_without_rewriting_old_evidence(self):
        original = self.event(task_id=None)
        issue = self.ledger.observe(original, "critical")
        linked = self.ledger.link_incident(issue, "report-1", "已核对原运行记录")
        self.assertEqual(linked["task_id"], "report-1")
        self.assertIsNone(linked["original_task_id"])
        self.assertEqual(linked["task_link_reason"], "已核对原运行记录")
        self.assertEqual(linked["evidence_ids"], [original.event_id])
        self.assertIsNone(original.task_id)
        # Reobserving the original record still matches its immutable snapshot.
        self.assertEqual(self.ledger.observe(original, "critical"), issue)
        self.assertIsNone(self.ledger._events[original.event_id]["task_id"])
        plan = self.ledger.propose_repair(issue)
        self.ledger.begin_repair(plan["id"], host_authorized=True)
        self.clock.advance()
        evidence = self.result(plan)
        self.ledger.observe(evidence, "low")
        self.ledger.complete_repair(plan["id"], "resolved", evidence)
        resolved = self.ledger.list_incidents(status="resolved")[0]
        self.assertEqual(resolved["id"], issue)
        self.assertEqual(resolved["task_id"], "report-1")
        self.assertIsNone(resolved["original_task_id"])

    def test_task_link_retry_is_idempotent_but_rebinding_is_rejected(self):
        issue = self.ledger.observe(self.event(task_id=None), "critical")
        linked = self.ledger.link_incident(issue, "report-1", "经核验关联")
        self.clock.advance()
        self.assertEqual(self.ledger.link_incident(issue, "report-1", "经核验关联"), linked)
        for task, reason in (("report-2", "经核验关联"), ("report-1", "different reason")):
            with self.subTest(task=task, reason=reason), self.assertRaisesRegex(ValueError, "rebound"):
                self.ledger.link_incident(issue, task, reason)
        plan = self.ledger.propose_repair(issue)
        self.ledger.begin_repair(plan["id"], host_authorized=True)
        # A no-op retry remains safe after a plan exists; its scope is unchanged.
        self.assertEqual(self.ledger.link_incident(issue, "report-1", "经核验关联"), linked)
        with self.assertRaises(ValueError):
            self.ledger.link_incident(issue, "report-2", "another task")

    def test_task_link_rejects_invalid_arguments_and_originally_scoped_issue(self):
        issue = self.ledger.observe(self.event(task_id=None), "critical")
        for task, reason in ((None, "valid"), (" ", "valid"), ("report-1", ""), ("report-1", None)):
            with self.subTest(task=task, reason=reason), self.assertRaises(ValueError):
                self.ledger.link_incident(issue, task, reason)
        scoped = self.ledger.observe(self.event(), "critical")
        with self.assertRaises(ValueError):
            self.ledger.link_incident(scoped, "report-1", "already scoped")
        with self.assertRaises(ValueError):
            self.ledger.link_incident("missing", "report-1", "valid")

    def test_plan_can_start_once_even_with_concurrent_requests(self):
        issue = self.ledger.observe(self.event(), "critical")
        plan = self.ledger.propose_repair(issue)

        def begin(_):
            try:
                self.ledger.begin_repair(plan["id"], host_authorized=True)
                return True
            except ValueError:
                return False

        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sum(pool.map(begin, range(8))), 1)

    def test_verified_recovery_resolves_only_original_issue(self):
        original, issue, plan = self.started()
        other = self.ledger.observe(self.event(task_id="report-2"), "critical")
        evidence = self.result(plan)
        # Historical aggregate risk may still be critical: positive evidence is
        # not a new adverse event, and can verify recovery of the original issue.
        self.assertIsNone(self.ledger.observe(evidence, "critical"))
        completed = self.ledger.complete_repair(plan["id"], "resolved", evidence)
        self.assertEqual(completed["state"], "completed")
        self.assertEqual(completed["evidence_event_id"], evidence.event_id)
        self.assertEqual(self.ledger.list_incidents(status="resolved")[0]["id"], issue)
        self.assertEqual([i["id"] for i in self.ledger.list_incidents(status="open")], [other])
        self.assertEqual(self.ledger.complete_repair(plan["id"], "resolved", evidence), completed)
        with self.assertRaises(ValueError):
            self.ledger.complete_repair(plan["id"], "failed", evidence)

    def test_unobserved_or_mutated_evidence_cannot_resolve(self):
        _, _, plan = self.started()
        evidence = self.result(plan)
        with self.assertRaisesRegex(ValueError, "previously observed"):
            self.ledger.complete_repair(plan["id"], "resolved", evidence)
        self.ledger.observe(evidence, "low")
        with self.assertRaisesRegex(ValueError, "previously observed"):
            self.ledger.complete_repair(plan["id"], "resolved", replace(evidence, impact_a=0.9))

    def test_unstarted_plan_cannot_complete(self):
        issue = self.ledger.observe(self.event(), "critical")
        plan = self.ledger.propose_repair(issue)
        evidence = self.result(plan)
        self.ledger.observe(evidence, "low")
        with self.assertRaisesRegex(ValueError, "has not started"):
            self.ledger.complete_repair(plan["id"], "resolved", evidence)

    def test_cross_task_pair_and_action_evidence_rejected(self):
        _, _, plan = self.started()
        for overrides in (
            {"task_id": "report-2"}, {"target": "stranger"},
            {"interaction_type": "help"},
            {"context": {"result": "success", "verification_passed": True, "repair_plan_id": "other"}},
        ):
            with self.subTest(overrides=overrides):
                evidence = self.result(plan, **overrides)
                self.ledger.observe(evidence, "low")
                with self.assertRaises(ValueError):
                    self.ledger.complete_repair(plan["id"], "resolved", evidence)
        self.assertEqual(len(self.ledger.list_incidents(status="resolved")), 0)

    def test_failed_verification_and_unknown_assessment_cannot_resolve(self):
        _, _, plan = self.started()
        for status in (False, None, 1):
            evidence = self.result(plan, context={
                "result": "success", "verification_passed": status,
                "repair_plan_id": plan["id"],
            })
            self.ledger.observe(evidence, "low")
            with self.assertRaisesRegex(ValueError, "successful, verified"):
                self.ledger.complete_repair(plan["id"], "resolved", evidence)
        unknown = self.result(plan, assessment_status="unknown", impact_a=None, impact_b=None)
        self.ledger.observe(unknown, "unknown")
        with self.assertRaises(ValueError):
            self.ledger.complete_repair(plan["id"], "resolved", unknown)
        self.assertEqual(len(self.ledger.list_incidents(status="resolved")), 0)

    def test_negative_or_goal_violating_evidence_cannot_resolve(self):
        for overrides in ({"impact_b": -0.1}, {"goal_status": "violated"}):
            with self.subTest(overrides=overrides):
                self.setUp()
                _, _, plan = self.started()
                evidence = self.result(plan, **overrides)
                self.ledger.observe(evidence, "low")
                with self.assertRaises(ValueError):
                    self.ledger.complete_repair(plan["id"], "resolved", evidence)

    def test_goal_violation_needs_confirmed_goal_recovery_not_unknown(self):
        _, issue, plan = self.started(
            goal_status="violated", impact_a=0.5, impact_b=0.5,
            context={"result": "success"},
        )
        evidence = self.result(plan, goal_status="unknown")
        self.ledger.observe(evidence, "low")
        with self.assertRaisesRegex(ValueError, "successful, verified"):
            self.ledger.complete_repair(plan["id"], "resolved", evidence)
        self.assertEqual(self.ledger.list_incidents(status="open")[0]["id"], issue)
        partial = self.ledger.complete_repair(plan["id"], "partial", evidence)
        self.assertEqual(partial["outcome"], "partial")
        self.assertEqual(len(self.ledger.list_incidents(status="resolved")), 0)

    def test_goal_violation_can_resolve_when_goal_is_met_and_host_verified(self):
        _, issue, plan = self.started(
            goal_status="violated", impact_a=0.5, impact_b=0.5,
            context={"result": "success"},
        )
        evidence = self.result(plan, goal_status="met")
        self.ledger.observe(evidence, "low")
        completed = self.ledger.complete_repair(plan["id"], "resolved", evidence)
        self.assertEqual(completed["outcome"], "resolved")
        self.assertEqual(self.ledger.list_incidents(status="resolved")[0]["id"], issue)

    def test_old_evidence_cannot_complete_new_plan(self):
        issue = self.ledger.observe(self.event(), "critical")
        plan = self.ledger.propose_repair(issue)
        old = self.result(plan)
        self.ledger.observe(old, "low")
        self.ledger.begin_repair(plan["id"], host_authorized=True)
        with self.assertRaisesRegex(ValueError, "new evidence"):
            self.ledger.complete_repair(plan["id"], "resolved", old)
        # Changing an ID does not turn an old timestamp into new evidence.
        self.clock.advance()
        old_timestamp = self.result(plan, timestamp=99)
        self.ledger.observe(old_timestamp, "low")
        with self.assertRaisesRegex(ValueError, "new evidence"):
            self.ledger.complete_repair(plan["id"], "resolved", old_timestamp)

    def test_new_adverse_evidence_prevents_stale_plan_closure(self):
        _, _, plan = self.started()
        self.ledger.observe(self.event(), "critical")
        evidence = self.result(plan)
        self.ledger.observe(evidence, "low")
        with self.assertRaisesRegex(ValueError, "new adverse evidence"):
            self.ledger.complete_repair(plan["id"], "resolved", evidence)
        partial = self.ledger.complete_repair(plan["id"], "partial", evidence)
        self.assertEqual(partial["outcome"], "partial")
        self.assertEqual(len(self.ledger.list_incidents(status="open")), 1)

    def test_cancelled_proposal_can_be_replaced_without_cooldown(self):
        issue = self.ledger.observe(self.event(), "critical")
        plan = self.ledger.propose_repair(issue)
        cancelled = self.ledger.cancel_repair(plan["id"])
        self.assertEqual(cancelled["state"], "cancelled")
        self.assertIsNone(cancelled["started_at"])
        self.assertEqual(self.ledger.cancel_repair(plan["id"]), cancelled)
        replacement = self.ledger.propose_repair(issue, "restricted_retest")
        self.assertNotEqual(replacement["id"], plan["id"])
        with self.assertRaises(ValueError):
            self.ledger.begin_repair(plan["id"], host_authorized=True)
        self.assertEqual(len(self.ledger.list_incidents(status="open")), 1)

    def test_cancel_started_stale_plan_consumes_attempt_without_fake_evidence(self):
        _, issue, plan = self.started()
        self.ledger.observe(self.event(), "critical")
        cancelled = self.ledger.cancel_repair(plan["id"])
        self.assertEqual(cancelled["state"], "cancelled")
        self.assertEqual(len(self.ledger.list_incidents(status="open")), 1)
        evidence = self.result(plan)
        self.ledger.observe(evidence, "low")
        with self.assertRaises(ValueError):
            self.ledger.complete_repair(plan["id"], "resolved", evidence)
        with self.assertRaisesRegex(ValueError, "cooldown"):
            self.ledger.propose_repair(issue)
        self.clock.advance(3600)
        replacement = self.ledger.propose_repair(issue)
        self.assertNotEqual(replacement["id"], plan["id"])
        self.ledger.begin_repair(replacement["id"], host_authorized=True)

    def test_completed_plan_cannot_be_cancelled_to_bypass_result(self):
        _, _, plan = self.started()
        evidence = self.result(plan)
        self.ledger.observe(evidence, "low")
        self.ledger.complete_repair(plan["id"], "resolved", evidence)
        with self.assertRaises(ValueError):
            self.ledger.cancel_repair(plan["id"])

    def test_failed_and_partial_attempts_apply_cooldown_at_any_severity(self):
        for outcome in ("failed", "partial"):
            with self.subTest(outcome=outcome):
                self.setUp()
                _, issue, plan = self.started(impact_a=-0.1, impact_b=-0.1)
                evidence = self.result(plan, impact_a=-0.1, impact_b=-0.1, context={
                    "result": "failed", "verification_passed": False,
                    "repair_plan_id": plan["id"],
                })
                self.ledger.observe(evidence, "high")
                self.ledger.complete_repair(plan["id"], outcome, evidence)
                with self.assertRaisesRegex(ValueError, "cooldown"):
                    self.ledger.propose_repair(issue)
                self.clock.advance(3599)
                with self.assertRaisesRegex(ValueError, "cooldown"):
                    self.ledger.propose_repair(issue)
                self.clock.advance()
                next_plan = self.ledger.propose_repair(issue)
                self.assertNotEqual(next_plan["id"], plan["id"])

    def test_returned_objects_and_original_records_are_detached(self):
        original, issue, plan = self.started()
        original.context["narrative"] = "modified later"
        issues = self.ledger.list_incidents()
        issues[0]["evidence_ids"].clear()
        issues[0]["reason"] = "overwritten"
        plan["agents"].append("intruder")
        plan["scope"] = "everything"
        current = self.ledger.list_incidents()[0]
        self.assertEqual(current["reason"], "资料版本不同")
        self.assertEqual(current["evidence_ids"], [original.event_id])
        self.assertNotIn("intruder", self.ledger.propose_repair(issue)["agents"])

    def test_clock_validation_and_backwards_clock(self):
        for bad in (float("nan"), float("inf"), -1, True, "123"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                RecoveryLedger(clock=lambda: bad)
        self.clock.value = 99
        with self.assertRaisesRegex(ValueError, "backwards"):
            self.ledger.observe(self.event(), "critical")

    def test_validation_rejects_invalid_action_state_and_future_event(self):
        with self.assertRaises(ValueError):
            self.ledger.observe(self.event(timestamp=1000), "critical")
        with self.assertRaises(ValueError):
            self.ledger.list_incidents(status="closed")
        issue = self.ledger.observe(self.event(), "critical")
        with self.assertRaises(ValueError):
            self.ledger.propose_repair(issue, "execute_everything")


if __name__ == "__main__":
    unittest.main()
