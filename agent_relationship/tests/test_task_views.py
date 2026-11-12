"""Task cards expose next steps without inventing execution or success."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent_relationship import RelationshipTracker
from agent_relationship.state import export_state, restore_state
from agent_relationship.task_views import project_queue, project_task


class TestTaskViews(unittest.TestCase):
    def setUp(self):
        self.tracker = RelationshipTracker()
        self.counter = 0

    def record(self, task="task-one", agents=("writer", "reviewer"), **fields):
        self.counter += 1
        return self.tracker.track(*agents, {
            "event_id": "event-" + str(self.counter), "task_id": task,
            "action": "handoff", "result": "failed",
            "narrative": "宿主报告：交接资料版本不同", **fields,
        })

    def snapshots(self):
        ledger = self.tracker._engine.recovery
        return (deepcopy(list(ledger._incidents.values())),
                deepcopy(list(ledger._plans.values())),
                {event_id: self.tracker.event(event_id) for event_id in self.tracker._engine.event_records})

    def view(self, task="task-one", now=10**12):
        return project_task(task, *self.snapshots(), now=now, cooldown_seconds=3600)

    def queue(self, now=10**12):
        return project_queue(*self.snapshots(), now=now, cooldown_seconds=3600)

    def start(self, result=None):
        result = result or self.record()
        plan = self.tracker.propose_repair(result.incident_id)
        self.tracker.begin_repair(plan["id"], host_authorized=True)
        return result.incident_id, plan

    def finish(self, plan, outcome="resolved"):
        verified = self.record(task=plan["task_id"], agents=plan["agents"],
                               action=plan["action"], scenario="success", result="success",
                               repair_plan_id=plan["id"], verification_passed=True)
        return self.tracker.complete_repair(plan["id"], outcome, verified.event_id)

    def test_missing_and_observed_tasks_are_not_reported_as_resolved(self):
        empty = self.view()
        self.assertEqual(empty["recovery_status"], "no_evidence")
        self.assertEqual(empty["primary_action"]["kind"], "record_evidence")
        self.assertEqual(empty["observed_event_count"], 0)
        self.record(result="success", scenario="success")
        observed = self.view()
        self.assertEqual(observed["recovery_status"], "observed")
        self.assertEqual(observed["summary"], "暂无已记录异常")
        self.assertEqual(observed["resolved_incident_count"], 0)
        self.assertEqual(self.queue()["tasks"], [])
        self.assertEqual(self.view("other")["recovery_status"], "no_evidence")

    def test_observation_and_assessment_remain_distinct_without_raw_payloads(self):
        first = self.record(narrative="first observation", hidden={"credential": "not-for-card"})
        second = self.record(narrative="latest host observation")
        view = self.view()
        card = view["incidents"][0]
        self.assertEqual(card["evidence_ids"], [first.event_id, second.event_id])
        self.assertEqual(card["observation"], {
            "event_id": second.event_id, "result": "failed", "narrative": "latest host observation"})
        self.assertEqual(card["assessment"]["source"], "mock")
        self.assertNotEqual(card["assessment"]["reason"], card["observation"]["narrative"])
        self.assertEqual(card["handling"]["state"], "needs_plan")
        self.assertEqual(view["primary_action"]["kind"], "prepare_repair")
        self.assertNotIn("context", str(view))
        self.assertNotIn("credential", str(view))
        self.assertNotIn("not-for-card", str(view))
        self.assertFalse(view["primary_action"]["grants_permission"])

    def test_proposed_and_started_are_authorization_and_receipt_states(self):
        result = self.record()
        plan = self.tracker.propose_repair(result.incident_id)
        proposed = self.view()
        self.assertEqual(proposed["incidents"][0]["handling"]["state"], "awaiting_authorization")
        self.assertEqual(proposed["primary_action"]["kind"], "review_proposal")
        self.assertEqual(proposed["active_plan_count"], 1)
        self.tracker.begin_repair(plan["id"], host_authorized=True)
        started = self.view()
        self.assertEqual(started["recovery_status"], "waiting")
        self.assertEqual(started["incidents"][0]["handling"]["state"], "awaiting_result")
        self.assertEqual(started["primary_action"]["kind"], "inspect_host_result")
        self.assertEqual(started["primary_action"]["plan_id"], plan["id"])
        self.assertNotIn("executing", str(started))

    def test_unknown_assessment_remains_an_evidence_issue(self):
        self.record(action="unsupported", result="success")
        view = self.view()
        self.assertEqual(view["recovery_status"], "needs_attention")
        self.assertEqual(view["open_incident_count"], 1)
        self.assertEqual(view["incidents"][0]["assessment"]["category"], "evidence_needed")
        self.assertEqual(view["incidents"][0]["assessment"]["goal_status"], "unknown")
        self.assertEqual(view["incidents"][0]["observation"]["result"], "success")
        self.assertEqual(view["primary_action"]["kind"], "prepare_repair")
        self.assertEqual(len(self.queue()["tasks"]), 1)

    def test_new_adverse_evidence_requires_reconciliation_of_started_plan(self):
        _, plan = self.start()
        self.record()
        view = self.view()
        self.assertEqual(view["recovery_status"], "needs_attention")
        self.assertEqual(view["incidents"][0]["handling"]["state"], "needs_reconciliation")
        self.assertEqual(view["primary_action"]["kind"], "reconcile_plan")
        self.assertEqual(view["primary_action"]["plan_id"], plan["id"])

    def test_failed_and_partial_outcomes_keep_identity_through_cooldown(self):
        for outcome in ("failed", "partial"):
            with self.subTest(outcome=outcome):
                self.tracker = RelationshipTracker()
                _, plan = self.start()
                completed = self.finish(plan, outcome)
                now = completed["completed_at"]
                cooling = self.view(now=now + 1)
                handling = cooling["incidents"][0]["handling"]
                self.assertEqual(handling["state"], "cooldown")
                self.assertEqual(handling["last_outcome"], outcome)
                self.assertEqual(handling["retry_after"], now + 3600)
                self.assertTrue(handling["cooldown_active"])
                self.assertEqual(cooling["primary_action"]["kind"], "review_previous_result")
                ready = self.view(now=now + 3600)
                self.assertEqual(ready["incidents"][0]["handling"]["state"], "ready_for_retry")
                self.assertFalse(ready["incidents"][0]["handling"]["cooldown_active"])
                self.assertEqual(ready["primary_action"]["kind"], "prepare_repair")
                self.assertEqual(ready["open_incident_count"], 1)
                self.assertEqual(len(self.tracker._engine.recovery._plans), 1)

    def test_terminal_plan_result_is_visible_without_changing_original_incident_evidence(self):
        for outcome in ("failed", "partial"):
            with self.subTest(outcome=outcome):
                self.tracker = RelationshipTracker()
                issue, plan = self.start()
                original_ids = self.tracker.incidents()[0]["evidence_ids"]
                completed = self.finish(plan, outcome)
                result_id = completed["evidence_event_id"]
                self.assertNotIn(result_id, self.tracker.incidents()[0]["evidence_ids"])
                card = self.view()["incidents"][0]
                self.assertEqual(card["id"], issue)
                self.assertEqual(card["evidence_ids"], original_ids + [result_id])
                self.assertEqual(card["observation"]["event_id"], original_ids[-1])
                self.assertEqual(card["handling"]["evidence_event_id"], result_id)
                self.assertEqual(card["handling"]["observation"]["event_id"], result_id)
                self.assertEqual(card["handling"]["observation"]["result"], "success")
                self.assertEqual(card["handling"]["assessment"]["source"], "mock")
                self.assertEqual(self.tracker.incidents()[0]["evidence_ids"], original_ids)
                incidents, plans, events = self.snapshots()
                del events[result_id]
                missing = project_task("task-one", incidents, plans, events, now=10**12, cooldown_seconds=3600)
                self.assertEqual(missing["recovery_status"], "needs_attention")
                self.assertEqual(missing["incidents"][0]["handling"]["state"], "evidence_missing")
                self.assertEqual(missing["missing_evidence_count"], 1)

    def test_equal_clock_terminal_plans_are_ambiguous_after_sorted_json_and_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tracker.sqlite3"
            with RelationshipTracker(storage_path=path) as tracker:
                self.tracker = tracker
                issue = self.record().incident_id
                ledger = tracker._engine.recovery
                now = ledger._last_clock
                # Both lifecycle times are equal, while the older UUID sorts
                # after the newer one. Neither UUID nor JSON key order is time.
                with patch.object(ledger, "_clock", lambda: now), patch(
                    "agent_relationship.recovery.uuid4",
                    side_effect=[SimpleNamespace(hex="f" * 32), SimpleNamespace(hex="0" * 32)],
                ):
                    older = tracker.propose_repair(issue, "verify_handoff")
                    tracker.cancel_repair(older["id"])
                    newer = tracker.propose_repair(issue, "restricted_retest")
                    tracker.cancel_repair(newer["id"])
                before = tracker.task_view("task-one")
                handling = before["incidents"][0]["handling"]
                self.assertEqual(handling["state"], "needs_reconciliation")
                self.assertEqual(handling["plan_ids"], sorted([older["id"], newer["id"]]))
                self.assertIsNone(handling["plan_id"])
                self.assertIsNone(handling["action"])
                self.assertIsNone(handling["last_outcome"])
                self.assertEqual(before["primary_action"]["kind"], "inspect_history")
                self.assertIsNone(before["primary_action"]["plan_id"])
                restored = RelationshipTracker()
                restore_state(restored, json.loads(json.dumps(export_state(tracker), sort_keys=True)))
                self.assertEqual(restored.task_view("task-one"), before)
            with RelationshipTracker(storage_path=path) as reopened:
                self.assertEqual(reopened.task_view("task-one"), before)
                active = reopened.propose_repair(issue)
                view = reopened.task_view("task-one")
                self.assertEqual(view["incidents"][0]["handling"]["state"], "awaiting_authorization")
                self.assertEqual(view["primary_action"]["plan_id"], active["id"])

    def test_ambiguous_history_preserves_every_candidate_outcome_reference(self):
        _, plan = self.start()
        completion = self.finish(plan, "partial")
        later_result = self.record(result="success", scenario="success")
        incidents, plans, events = self.snapshots()
        other = deepcopy(plans[0])
        other.update(id="other-terminal-plan", outcome="failed", evidence_event_id=later_result.event_id)
        plans.append(other)
        before = deepcopy((incidents, plans, events))
        view = project_task("task-one", incidents, plans, events, now=10**12, cooldown_seconds=3600)
        card = view["incidents"][0]
        self.assertEqual(view["primary_action"]["kind"], "inspect_history")
        self.assertIsNone(card["handling"]["evidence_event_id"])
        self.assertIsNone(card["handling"]["observation"])
        self.assertIsNone(card["handling"]["assessment"])
        expected_refs = sorted([completion["evidence_event_id"], later_result.event_id])
        self.assertEqual(card["handling"]["evidence_event_ids"], expected_refs)
        self.assertTrue(set(expected_refs).issubset(card["evidence_ids"]))
        self.assertEqual((incidents, plans, events), before)
        del events[later_result.event_id]
        missing = project_task("task-one", incidents, plans, events, now=10**12, cooldown_seconds=3600)
        self.assertEqual(missing["incidents"][0]["handling"]["state"], "evidence_missing")
        self.assertEqual(missing["primary_action"]["kind"], "inspect_evidence")

    def test_cancellation_before_and_after_start_are_distinguished(self):
        result = self.record()
        proposal = self.tracker.propose_repair(result.incident_id)
        self.tracker.cancel_repair(proposal["id"])
        card = self.view()["incidents"][0]
        self.assertEqual(card["handling"]["state"], "cancelled")
        self.assertEqual(card["handling"]["last_outcome"], "cancelled")
        self.assertIsNone(card["handling"]["retry_after"])
        _, started = self.start(result)
        cancelled = self.tracker.cancel_repair(started["id"])
        cooling = self.view(now=cancelled["cancelled_at"])
        self.assertEqual(cooling["incidents"][0]["handling"]["state"], "cooldown")
        self.assertEqual(cooling["incidents"][0]["handling"]["last_outcome"], "cancelled")
        self.assertEqual(cooling["primary_action"]["plan_id"], started["id"])

    def test_resolution_returns_to_task_without_authorizing_or_completing_it(self):
        issue, plan = self.start()
        completion = self.finish(plan)
        view = self.view()
        self.assertEqual(view["recovery_status"], "resolved")
        self.assertEqual(view["active_plan_count"], 0)
        self.assertEqual(view["open_incident_count"], 0)
        self.assertEqual(view["resolved_incident_count"], 1)
        self.assertEqual(view["primary_action"]["kind"], "return_to_task")
        self.assertFalse(view["primary_action"]["grants_permission"])
        self.assertEqual(view["incidents"][0]["id"], issue)
        self.assertEqual(view["incidents"][0]["agents"], plan["agents"])
        self.assertEqual(view["incidents"][0]["resolution_event_id"], completion["evidence_event_id"])
        self.assertNotIn("task_completed", view)
        self.assertEqual(self.queue()["tasks"], [])

    def test_all_pairs_block_task_resolution_but_other_tasks_stay_separate(self):
        _, plan = self.start()
        second = self.record(agents=("organizer", "auditor"))
        self.record(task="other-task")
        self.finish(plan)
        view = self.view()
        self.assertEqual(view["open_incident_count"], 1)
        self.assertEqual(view["resolved_incident_count"], 1)
        self.assertEqual(view["primary_incident_id"], second.incident_id)
        self.assertEqual(view["recovery_status"], "needs_attention")
        self.assertEqual({row["task_id"] for row in self.queue()["tasks"]}, {"task-one", "other-task"})
        self.assertEqual(self.view("other-task")["resolved_incident_count"], 0)

    def test_unlinked_issues_are_visible_then_linked_without_rewriting_evidence(self):
        result = self.record(task=None)
        unlinked = self.queue()["unlinked_incidents"]
        self.assertEqual(len(unlinked), 1)
        self.assertEqual(unlinked[0]["handling"]["state"], "needs_task_link")
        self.assertEqual(unlinked[0]["next_action"]["kind"], "link_task")
        self.assertEqual(self.queue()["tasks"], [])
        self.tracker.link_incident(result.incident_id, "task-one", "宿主核对原运行")
        self.assertEqual(self.queue()["unlinked_incidents"], [])
        view = self.view()
        self.assertEqual(view["observed_event_count"], 1)
        self.assertIsNone(view["incidents"][0]["original_task_id"])
        self.assertEqual(view["incidents"][0]["task_link_reason"], "宿主核对原运行")
        self.assertIsNone(self.tracker.event(result.event_id)["task_id"])

    def test_queue_is_compact_deterministic_and_prioritizes_actionable_issues(self):
        waiting = self.record(task="waiting")
        self.start(waiting)
        self.record(task="actionable")
        queue = self.queue()
        self.assertEqual([row["task_id"] for row in queue["tasks"]], ["actionable", "waiting"])
        self.assertTrue(all("incidents" not in row for row in queue["tasks"]))
        self.assertEqual(queue, self.queue())
        self.assertEqual(queue["tasks"][1]["primary_action"]["kind"], "inspect_host_result")

    def test_missing_referenced_evidence_cannot_be_rendered_as_verified_resolution(self):
        _, plan = self.start()
        complete = self.finish(plan)
        incidents, plans, events = self.snapshots()
        del events[complete["evidence_event_id"]]
        view = project_task("task-one", incidents, plans, events, now=10**12, cooldown_seconds=3600)
        self.assertEqual(view["recovery_status"], "needs_attention")
        self.assertEqual(view["missing_evidence_count"], 1)
        self.assertEqual(view["primary_action"]["kind"], "inspect_evidence")
        queue = project_queue(incidents, plans, events, now=10**12, cooldown_seconds=3600)
        self.assertEqual(len(queue["tasks"]), 1)
        self.assertEqual(queue["tasks"][0]["task_id"], "task-one")
        self.assertEqual(queue["tasks"][0]["primary_action"]["kind"], "inspect_evidence")

    def test_nontext_and_long_payloads_are_safe_card_summaries(self):
        result = self.record(narrative={"content": "not a string"})
        self.assertEqual(self.view()["incidents"][0]["observation"]["narrative"], "")
        self.record(narrative="x" * 1000)
        card = self.view()["incidents"][0]
        self.assertEqual(len(card["observation"]["narrative"]), 240)
        self.assertTrue(card["observation"]["narrative"].endswith("…"))
        incidents, plans, events = self.snapshots()
        events[result.event_id]["result"] = {"unexpected": True}
        # Projection does not stringify arbitrary dictionaries into the card.
        incidents[0]["evidence_ids"] = [result.event_id]
        view = project_task("task-one", incidents, plans, events, now=0, cooldown_seconds=3600)
        self.assertIsNone(view["incidents"][0]["observation"]["result"])

    def test_projection_is_detached_and_does_not_mutate_inputs_or_live_state(self):
        self.start()
        inputs = self.snapshots()
        baseline = deepcopy(inputs)
        live = export_state(self.tracker)
        view = project_task("task-one", *inputs, now=10**12, cooldown_seconds=3600)
        queue = project_queue(*inputs, now=10**12, cooldown_seconds=3600)
        view["incidents"][0]["agents"].append("extra")
        view["incidents"][0]["evidence_ids"].clear()
        view["primary_action"]["kind"] = "execute"
        queue["tasks"][0]["primary_action"]["kind"] = "execute"
        self.assertEqual(inputs, baseline)
        self.assertEqual(export_state(self.tracker), live)


if __name__ == "__main__":
    unittest.main()
