"""The reference host reconnects original steps without repeating local effects."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from agent_relationship import RelationshipTracker
from examples.host_task_recovery import HostContinuationBlocked, LocalTaskHost


class TestHostIntegration(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.sdk_path = Path(self.directory.name) / "relationship.sqlite3"
        self.host_path = Path(self.directory.name) / "host.sqlite3"
        self.tracker = RelationshipTracker(storage_path=self.sdk_path)
        self.host = LocalTaskHost(self.host_path)
        self.addCleanup(self.tracker.close)
        self.addCleanup(self.host.close)
        self.task_id, self.step_id = "original-report", "checked-draft"
        self.host.register_waiting_task(self.task_id, self.step_id)

    def failed(self, event_id="failure", *, task_id=None, pair=("a", "b")):
        return self.tracker.track(*pair, {
            "event_id": event_id, "task_id": task_id or self.task_id,
            "action": "handoff", "result": "failed", "narrative": "host-observed failure",
        }).incident_id

    def resolve(self, incident_id, *, pair=("a", "b"), outcome="resolved"):
        plan = self.tracker.propose_repair(incident_id)
        self.tracker.begin_repair(plan["id"], host_authorized=True)
        event_id = "verification-" + plan["id"]
        self.tracker.track(*pair, {
            "event_id": event_id, "task_id": plan["task_id"],
            "action": plan["action"], "repair_plan_id": plan["id"],
            "scenario": "success", "result": "success", "verification_passed": True,
        })
        return self.tracker.complete_repair(plan["id"], outcome, event_id)

    def continue_task(self, **kwargs):
        return self.host.continue_task(self.tracker, self.task_id, self.step_id, **kwargs)

    def assert_no_effect(self):
        self.assertIsNone(self.host.receipt(self.task_id, self.step_id))
        self.assertEqual(self.host.task(self.task_id)["state"], "waiting")
        self.assertIsNone(self.host.task(self.task_id)["result"])

    def test_verified_recovery_is_not_host_task_completion_and_needs_authorization(self):
        self.resolve(self.failed())
        self.assertEqual(self.tracker.task_view(self.task_id)["recovery_status"], "resolved")
        self.assert_no_effect()
        for authorization in (False, None, 1, "yes"):
            with self.subTest(authorization=authorization), self.assertRaises(HostContinuationBlocked):
                self.continue_task(host_authorized=authorization)
            self.assert_no_effect()
        receipt = self.continue_task(host_authorized=True)
        self.assertEqual(receipt["task_id"], self.task_id)
        self.assertEqual(receipt["blocked_step_id"], self.step_id)
        self.assertEqual(receipt["revision"], 1)
        self.assertEqual(self.host.task(self.task_id)["state"], "continued")
        self.assertEqual(self.host.task(self.task_id)["result"], receipt["result"])

    def test_restart_and_lost_response_return_the_same_committed_receipt(self):
        self.resolve(self.failed())
        receipt = self.continue_task(host_authorized=True)
        self.host.close()
        self.tracker.close()
        with RelationshipTracker(storage_path=self.sdk_path) as tracker, LocalTaskHost(self.host_path) as host:
            self.assertEqual(host.continue_task(tracker, self.task_id, self.step_id), receipt)
            self.assertEqual(host.receipt(self.task_id, self.step_id), receipt)
            self.assertEqual(host.task(self.task_id)["result"], receipt["result"])

    def test_all_incidents_across_agent_pairs_must_be_resolved(self):
        first = self.failed()
        other = self.failed("other-pair", pair=("c", "d"))
        self.resolve(first)
        with self.assertRaises(HostContinuationBlocked):
            self.continue_task(host_authorized=True)
        self.assert_no_effect()
        self.resolve(other, pair=("c", "d"))
        receipt = self.continue_task(host_authorized=True)
        self.assertEqual({item["incident_id"] for item in receipt["recovery_evidence"]}, {first, other})

    def test_partial_failed_and_unfinished_recovery_never_dispatch(self):
        incident = self.failed()
        plan = self.tracker.propose_repair(incident)
        with self.assertRaises(HostContinuationBlocked):
            self.continue_task(host_authorized=True)
        self.tracker.begin_repair(plan["id"], host_authorized=True)
        with self.assertRaises(HostContinuationBlocked):
            self.continue_task(host_authorized=True)
        self.assert_no_effect()
        for outcome in ("partial", "failed"):
            task_id = "task-" + outcome
            self.host.register_waiting_task(task_id, self.step_id)
            self.resolve(self.failed(outcome, task_id=task_id), outcome=outcome)
            with self.subTest(outcome=outcome), self.assertRaises(HostContinuationBlocked):
                self.host.continue_task(self.tracker, task_id, self.step_id, host_authorized=True)
            self.assertIsNone(self.host.receipt(task_id, self.step_id))

    def test_no_evidence_or_only_positive_observations_do_not_imply_recovery(self):
        with self.assertRaises(HostContinuationBlocked):
            self.continue_task(host_authorized=True)
        for i in range(3):
            self.tracker.track("a", "b", {"event_id": str(i), "task_id": self.task_id,
                                           "action": "handoff", "result": "success", "scenario": "success"})
        self.assertEqual(self.tracker.task_view(self.task_id)["recovery_status"], "observed")
        with self.assertRaises(HostContinuationBlocked):
            self.continue_task(host_authorized=True)
        self.assert_no_effect()

    def test_stale_card_cannot_dispatch_after_new_adverse_evidence(self):
        self.resolve(self.failed())
        displayed = self.tracker.task_view(self.task_id)
        self.assertEqual(displayed["recovery_status"], "resolved")
        self.failed("new-adverse-evidence")
        with self.assertRaises(HostContinuationBlocked):
            self.continue_task(host_authorized=True)
        self.assert_no_effect()

    def test_current_host_step_and_revision_cannot_be_rebound(self):
        self.resolve(self.failed())
        for task_id, step_id, revision in (("another-task", self.step_id, 1),
                                           (self.task_id, "another-step", 1),
                                           (self.task_id, self.step_id, 2)):
            with self.subTest(task_id=task_id, step_id=step_id, revision=revision), self.assertRaises(HostContinuationBlocked):
                self.host.continue_task(self.tracker, task_id, step_id, revision, host_authorized=True)
        with self.assertRaises(HostContinuationBlocked):
            self.host.register_waiting_task(self.task_id, "another-step", 2)
        self.assert_no_effect()

    def test_receipt_identity_is_original_step_not_later_recovery_plan(self):
        first_plan = self.resolve(self.failed())
        receipt = self.continue_task(host_authorized=True)
        # A second incident/recovery on the same task must not create another
        # effect identity or allow the already completed original step to run.
        second_plan = self.resolve(self.failed("later-failure"))
        self.assertNotEqual(first_plan["id"], second_plan["id"])
        self.assertEqual(self.continue_task(host_authorized=True), receipt)
        with sqlite3.connect(self.host_path) as database:
            self.assertEqual(database.execute("SELECT COUNT(*) FROM host_receipts").fetchone()[0], 1)

    def test_existing_receipt_can_be_read_when_new_incident_blocks_further_work(self):
        self.resolve(self.failed())
        receipt = self.continue_task(host_authorized=True)
        self.failed("later-unresolved")
        # This is result retrieval, not authorization for a fresh dispatch.
        self.assertEqual(self.continue_task(), receipt)
        self.assertEqual(self.host.receipt(self.task_id, self.step_id), receipt)
        self.assertEqual(len(self.tracker.incidents(task_id=self.task_id, status="open")), 1)

    def test_unknown_external_outcome_is_persisted_and_not_blindly_retried(self):
        self.resolve(self.failed())
        self.host.mark_outcome_unknown(self.task_id, self.step_id)
        self.host.close()
        with LocalTaskHost(self.host_path) as host:
            registered = host.register_waiting_task(self.task_id, self.step_id)
            self.assertEqual(registered["state"], "reconcile_required")
            with self.assertRaisesRegex(HostContinuationBlocked, "reconcile"):
                host.continue_task(self.tracker, self.task_id, self.step_id, host_authorized=True)
            self.assertIsNone(host.receipt(self.task_id, self.step_id))

    def test_independent_host_instances_commit_one_local_effect(self):
        self.resolve(self.failed())
        barrier = threading.Barrier(2)
        def run(_):
            with LocalTaskHost(self.host_path) as host:
                barrier.wait(5)
                return host.continue_task(self.tracker, self.task_id, self.step_id, host_authorized=True)
        with ThreadPoolExecutor(max_workers=2) as pool:
            receipts = list(pool.map(run, range(2)))
        self.assertEqual(receipts[0], receipts[1])
        with sqlite3.connect(self.host_path) as database:
            self.assertEqual(database.execute("SELECT COUNT(*) FROM host_receipts").fetchone()[0], 1)

    def test_host_commit_failure_rolls_back_result_and_receipt(self):
        self.resolve(self.failed())
        with patch.object(self.host, "_commit", side_effect=RuntimeError("injected commit failure")):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                self.continue_task(host_authorized=True)
        self.assert_no_effect()
        with LocalTaskHost(self.host_path) as reopened:
            self.assertEqual(reopened.task(self.task_id)["state"], "waiting")
            self.assertIsNone(reopened.receipt(self.task_id, self.step_id))
        self.assertEqual(self.continue_task(host_authorized=True)["state"], "completed")

    def test_failed_sdk_read_does_not_change_the_host_task(self):
        with patch.object(self.tracker, "task_view", side_effect=RuntimeError("unavailable evidence")):
            with self.assertRaisesRegex(RuntimeError, "unavailable"):
                self.continue_task(host_authorized=True)
        self.assert_no_effect()

    def run_crashing_host(self, when):
        script = '''
import os,sys
from agent_relationship import RelationshipTracker
from examples.host_task_recovery import LocalTaskHost
with RelationshipTracker(storage_path=sys.argv[1]) as tracker, LocalTaskHost(sys.argv[2]) as host:
    if sys.argv[3] == "before":
        host._commit = lambda: os._exit(9)
    host.continue_task(tracker, "original-report", "checked-draft", host_authorized=True)
    os._exit(9)
'''
        process = subprocess.run([sys.executable, "-c", script, str(self.sdk_path), str(self.host_path), when],
                                 capture_output=True, text=True, timeout=15)
        self.assertEqual(process.returncode, 9, process.stdout + process.stderr)

    def test_abrupt_exit_before_commit_does_not_leave_a_result_or_receipt(self):
        self.resolve(self.failed())
        self.run_crashing_host("before")
        self.assert_no_effect()
        self.assertEqual(self.continue_task(host_authorized=True)["state"], "completed")

    def test_abrupt_exit_after_commit_recovers_the_receipt_without_new_effect(self):
        self.resolve(self.failed())
        self.run_crashing_host("after")
        receipt = self.host.receipt(self.task_id, self.step_id)
        self.assertIsNotNone(receipt)
        self.assertEqual(self.continue_task(), receipt)
        self.assertEqual(self.host.task(self.task_id)["result"], receipt["result"])

    def test_host_refuses_to_add_tables_to_the_sdk_database(self):
        incident = self.failed()
        with self.assertRaisesRegex(ValueError, "own database"):
            LocalTaskHost(self.sdk_path)
        self.assertEqual(self.tracker.incidents()[0]["id"], incident)

    def test_invalid_host_identity_is_rejected_without_mutation(self):
        for task_id, step_id, revision in (("", self.step_id, 1), (self.task_id, " ", 1),
                                           (self.task_id, self.step_id, True), (self.task_id, self.step_id, 0)):
            with self.subTest(task_id=task_id, step_id=step_id, revision=revision), self.assertRaises(ValueError):
                self.host.register_waiting_task(task_id, step_id, revision)
        self.assert_no_effect()


if __name__ == "__main__":
    unittest.main()
