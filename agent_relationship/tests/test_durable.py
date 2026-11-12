"""Durability acceptance through the public tracker API, without model/network calls."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import multiprocessing
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from agent_relationship import RelationshipTracker
from agent_relationship.llm import MockLLM
from agent_relationship.state import StateError
from agent_relationship.storage import StorageError


class CountingMock(MockLLM):
    def __init__(self):
        self.calls = 0
        self.api_key = "backend-credential-never-serialize"

    def assess_interaction(self, *args, **kwargs):
        self.calls += 1
        return super().assess_interaction(*args, **kwargs)


def data(event_id, **kwargs):
    return {"event_id": event_id, "task_id": "original-task", "action": "handoff",
            "result": "success", "narrative": "host observation", **kwargs}


def record_worker(path, prefix, ready):
    with RelationshipTracker(storage_path=path) as tracker:
        ready.wait(10)
        tracker.track("a", "b", data("shared"))
        for i in range(5):
            tracker.track("a", "b", data(f"{prefix}-{i}"))


class TestDurableTracker(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "state.sqlite3"

    def test_restart_preserves_exact_evidence_and_dedup_without_model_replay(self):
        first_model = CountingMock()
        with RelationshipTracker(llm=first_model, storage_path=self.path, max_history=2) as tracker:
            initial = tracker.track("a", "b", data("failed", result="failed"))
            for i in range(4):
                tracker.track("a", "b", data(str(i)))
            before_health = tracker.health("a", "b")
            before_history = tracker.history("a", "b")
            before_issues = tracker.incidents()
        second_model = CountingMock()
        with RelationshipTracker(llm=second_model, storage_path=self.path, max_history=2) as tracker:
            self.assertEqual(tracker.health("a", "b"), before_health)
            self.assertEqual(tracker.history("a", "b"), before_history)
            self.assertEqual(tracker.incidents(), before_issues)
            retry = tracker.track("a", "b", data("failed", result="failed"))
            self.assertTrue(retry.duplicate)
            self.assertEqual(retry.incident_id, initial.incident_id)
            self.assertEqual(second_model.calls, 0)
            self.assertEqual(tracker.event("failed")["result"], "failed")
            self.assertFalse(tracker.can_cooperate("a", "b", task_id="original-task")["can"])
            with self.assertRaises(ValueError):
                tracker.track("a", "b", data("failed", result="success"))

    def test_restart_in_middle_of_recovery_cannot_consume_attempt_twice(self):
        with RelationshipTracker(storage_path=self.path) as tracker:
            incident = tracker.track("a", "b", data("failed", result="failed")).incident_id
            plan = tracker.propose_repair(incident)
            tracker.begin_repair(plan["id"], host_authorized=True)
        with RelationshipTracker(storage_path=self.path) as tracker:
            self.assertEqual(tracker.propose_repair(incident)["state"], "started")
            with self.assertRaises(ValueError):
                tracker.begin_repair(plan["id"], host_authorized=True)
            tracker.track("a", "b", data("verification", action="verify_handoff", scenario="success",
                                          repair_plan_id=plan["id"], verification_passed=True))
            complete = tracker.complete_repair(plan["id"], "resolved", "verification")
        with RelationshipTracker(storage_path=self.path) as tracker:
            self.assertEqual(tracker.incidents(status="open"), [])
            self.assertEqual(tracker.incidents(status="resolved")[0]["task_id"], "original-task")
            self.assertEqual(tracker.complete_repair(plan["id"], "resolved", "verification"), complete)

    def test_unknown_and_callback_failure_survive_restart_without_repeat_notification(self):
        alerts = []
        def callback(*args):
            alerts.append(args)
            raise RuntimeError("private-callback-detail")
        with RelationshipTracker(storage_path=self.path, on_risk_change=callback) as tracker:
            result = tracker.track("a", "b", data("bad", result="failed"))
            self.assertEqual(result.notification_error, "callback_failed")
            tracker.track("a", "b", data("unknown", action="unsupported"))
        count = len(alerts)
        with RelationshipTracker(storage_path=self.path, on_risk_change=callback) as tracker:
            self.assertEqual(tracker.health("a", "b").risk, "unknown")
            self.assertIsNone(tracker.network().pairs[("a", "b")])
            self.assertEqual(tracker.track("a", "b", data("bad", result="failed")).notification_error,
                             "callback_failed")
        self.assertEqual(len(alerts), count)

    def test_independent_instances_refresh_reads_and_preserve_other_writes(self):
        with RelationshipTracker(storage_path=self.path) as first, RelationshipTracker(storage_path=self.path) as second:
            first.track("a", "b", data("one"))
            self.assertEqual(second.health("a", "b").interaction_count, 1)
            second.track("a", "b", data("two", result="failed"))
            self.assertEqual(first.health("a", "b").interaction_count, 2)
            self.assertEqual(len(first.incidents()), 1)
            self.assertEqual(first.summary(), second.summary())

    def test_two_processes_deduplicate_shared_event_without_lost_updates(self):
        context = multiprocessing.get_context("spawn")
        ready = context.Event()
        workers = [context.Process(target=record_worker, args=(str(self.path), str(i), ready)) for i in range(2)]
        for worker in workers:
            worker.start()
        ready.set()
        for worker in workers:
            worker.join(20)
            if worker.is_alive():
                worker.terminate()
                worker.join(5)
            self.assertEqual(worker.exitcode, 0)
        with RelationshipTracker(storage_path=self.path) as tracker:
            self.assertEqual(tracker.health("a", "b").interaction_count, 11)

    def test_only_one_instance_can_begin_a_shared_plan(self):
        with RelationshipTracker(storage_path=self.path) as first, RelationshipTracker(storage_path=self.path) as second:
            issue = first.track("a", "b", data("bad", result="failed")).incident_id
            plan = first.propose_repair(issue)
            barrier = threading.Barrier(2)
            def begin(tracker):
                barrier.wait(5)
                try:
                    tracker.begin_repair(plan["id"], host_authorized=True)
                    return True
                except ValueError:
                    return False
            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(begin, [first, second]))
            self.assertEqual(sum(outcomes), 1)

    def test_failed_checkpoint_rolls_back_memory_and_disk_before_notification(self):
        alerts = []
        with RelationshipTracker(storage_path=self.path, on_risk_change=lambda *args: alerts.append(args)) as tracker:
            tracker.track("a", "b", data("before"))
            before = tracker.health("a", "b")
            original_transaction = tracker._storage.transaction
            @contextmanager
            def failing_transaction(write=False):
                with original_transaction(write=write) as transaction:
                    class BrokenSave:
                        def load(self):
                            return transaction.load()
                        def save(self, state):
                            raise StorageError("injected disk failure")
                    yield BrokenSave()
            with patch.object(tracker._storage, "transaction", failing_transaction):
                with self.assertRaises(StorageError):
                    tracker.track("a", "b", data("uncommitted", result="failed"))
            self.assertEqual(tracker.health("a", "b"), before)
            self.assertIsNone(tracker.event("uncommitted"))
            self.assertEqual(alerts, [])
        with RelationshipTracker(storage_path=self.path) as tracker:
            self.assertIsNone(tracker.event("uncommitted"))
            self.assertEqual(tracker.health("a", "b").interaction_count, 1)

    def test_abrupt_exit_during_assessment_leaves_previous_commit_intact(self):
        with RelationshipTracker(storage_path=self.path) as tracker:
            tracker.track("a", "b", data("before"))
        script = '''
import os,sys
from agent_relationship import RelationshipTracker
from agent_relationship.llm import MockLLM
class ExitDuringAssessment(MockLLM):
    def assess_interaction(self,*args,**kwargs): os._exit(9)
with RelationshipTracker(llm=ExitDuringAssessment(),storage_path=sys.argv[1]) as tracker:
    tracker.track("a","b",{"event_id":"uncommitted","action":"help","result":"success"})
'''
        result = subprocess.run([sys.executable, "-c", script, str(self.path)], capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 9)
        with RelationshipTracker(storage_path=self.path) as tracker:
            self.assertEqual(tracker.health("a", "b").interaction_count, 1)
            self.assertIsNone(tracker.event("uncommitted"))

    def test_backend_cannot_reenter_mutation_or_notify_before_commit(self):
        alerts = []
        class ReentrantBackend(MockLLM):
            def assess_interaction(self, *args, **kwargs):
                tracker.track("c", "d", data("inner", result="failed"))
                return super().assess_interaction(*args, **kwargs)
        with RelationshipTracker(llm=ReentrantBackend(), storage_path=self.path,
                                 on_risk_change=lambda *args: alerts.append(args)) as tracker:
            with self.assertRaisesRegex(RuntimeError, "reenter"):
                tracker.track("a", "b", data("outer", result="failed"))
            self.assertIsNone(tracker.event("inner"))
            self.assertIsNone(tracker.event("outer"))
            self.assertEqual(alerts, [])
        with RelationshipTracker(storage_path=self.path) as reopened:
            self.assertIsNone(reopened.event("inner"))
            self.assertIsNone(reopened.event("outer"))

    def test_committed_event_survives_exit_without_close(self):
        script = '''
import os,sys
from agent_relationship import RelationshipTracker
t=RelationshipTracker(storage_path=sys.argv[1])
t.track("a","b",{"event_id":"committed","action":"help","result":"success"})
os._exit(0)
'''
        result = subprocess.run([sys.executable, "-c", script, str(self.path)], capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        with RelationshipTracker(storage_path=self.path) as tracker:
            self.assertEqual(tracker.health("a", "b").interaction_count, 1)
            self.assertTrue(tracker.track("a", "b", {"event_id": "committed", "action": "help", "result": "success"}).duplicate)

    def test_configuration_mismatch_does_not_reset_state(self):
        with RelationshipTracker(storage_path=self.path, max_history=2) as tracker:
            tracker.track("a", "b", data("one"))
        with self.assertRaises(StateError):
            RelationshipTracker(storage_path=self.path, max_history=100)
        with RelationshipTracker(storage_path=self.path, max_history=2) as tracker:
            self.assertEqual(tracker.health("a", "b").interaction_count, 1)

    def test_cancellation_cooldown_persists(self):
        with RelationshipTracker(storage_path=self.path) as tracker:
            issue = tracker.track("a", "b", data("bad", result="failed")).incident_id
            plan = tracker.propose_repair(issue)
            tracker.begin_repair(plan["id"], host_authorized=True)
            cancelled = tracker.cancel_repair(plan["id"])
        with RelationshipTracker(storage_path=self.path) as tracker:
            self.assertEqual(tracker.cancel_repair(plan["id"]), cancelled)
            with self.assertRaises(ValueError):
                tracker.propose_repair(issue)
            self.assertEqual(tracker.incidents()[0]["status"], "open")

    def test_reads_and_duplicate_retries_do_not_rewrite_checkpoint(self):
        with RelationshipTracker(llm=CountingMock(), storage_path=self.path) as tracker:
            tracker.track("a", "b", data("one"))
            with tracker._storage.transaction() as tx:
                tx.load()
                revision = tx.revision
            tracker.summary()
            tracker.history("a", "b")
            tracker.can_cooperate("a", "b")
            tracker.track("a", "b", data("one"))
            with tracker._storage.transaction() as tx:
                payload = tx.load()
                self.assertEqual(tx.revision, revision)
            self.assertNotIn("backend-credential-never-serialize", json.dumps(payload))

    def test_close_and_lowlevel_mutation_guards(self):
        tracker = RelationshipTracker(storage_path=self.path)
        with self.assertRaises(RuntimeError):
            _ = tracker.engine
        tracker.close()
        tracker.close()
        with self.assertRaises(RuntimeError):
            tracker.health("a", "b")
        with self.assertRaises(RuntimeError):
            tracker.track("a", "b", data("closed"))


if __name__ == "__main__":
    unittest.main()
