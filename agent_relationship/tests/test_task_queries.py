"""Public task reads remain fresh, detached and free of durable mutations."""

from pathlib import Path
import tempfile
import unittest

from agent_relationship import RelationshipTracker
from agent_relationship.llm import MockLLM
from agent_relationship.state import export_state


class CountingBackend(MockLLM):
    def __init__(self):
        self.calls = 0

    def assess_interaction(self, *args, **kwargs):
        self.calls += 1
        return super().assess_interaction(*args, **kwargs)


def failure(event_id="failure", task_id="original-task"):
    return {"event_id": event_id, "task_id": task_id, "action": "handoff",
            "result": "failed", "narrative": "宿主报告资料版本未对齐"}


class TestTaskQueries(unittest.TestCase):
    def test_all_reads_preserve_checkpoint_clock_and_avoid_backend_or_callbacks(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = CountingBackend()
            callbacks = []
            with RelationshipTracker(llm=backend, storage_path=Path(directory) / "state.sqlite3",
                                     on_risk_change=lambda *args: callbacks.append(args)) as tracker:
                issue = tracker.track("writer", "reviewer", failure()).incident_id
                plan = tracker.propose_repair(issue)
                tracker.begin_repair(plan["id"], host_authorized=True)
                before = export_state(tracker)
                revision = tracker._storage.revision
                calls, alerts = backend.calls, len(callbacks)
                for _ in range(2):
                    view = tracker.task_view("original-task")
                    self.assertEqual(view["open_incident_count"], 1)
                    self.assertEqual(view["active_plan_count"], 1)
                    self.assertEqual(len(tracker.task_queue()["tasks"]), 1)
                    self.assertEqual(tracker.repair_plan(plan["id"])["state"], "started")
                    self.assertEqual(len(tracker.repair_plans(task_id="original-task", incident_id=issue)), 1)
                self.assertEqual(export_state(tracker), before)
                self.assertEqual(tracker._storage.revision, revision)
                self.assertEqual((backend.calls, len(callbacks)), (calls, alerts))

    def test_queries_refresh_after_other_instance_mutates_and_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "state.sqlite3"
            with RelationshipTracker(storage_path=database) as writer, RelationshipTracker(storage_path=database) as reader:
                self.assertEqual(reader.task_view("original-task")["recovery_status"], "no_evidence")
                issue = writer.track("writer", "reviewer", failure()).incident_id
                plan = writer.propose_repair(issue)
                writer.begin_repair(plan["id"], host_authorized=True)
                self.assertEqual(reader.repair_plan(plan["id"])["state"], "started")
                before = reader.task_view("original-task")
                self.assertEqual(before["active_plan_count"], 1)
                self.assertEqual(reader.repair_plans(task_id="other-task"), [])
                self.assertIsNone(reader.repair_plan("missing-plan"))
            with RelationshipTracker(storage_path=database) as reopened:
                self.assertEqual(reopened.task_view("original-task"), before)
                self.assertEqual(reopened.repair_plan(plan["id"])["state"], "started")

    def test_read_results_cannot_mutate_task_evidence_or_plans(self):
        tracker = RelationshipTracker()
        issue = tracker.track("writer", "reviewer", failure()).incident_id
        plan = tracker.propose_repair(issue)
        before = export_state(tracker)
        tracker.repair_plan(plan["id"])["agents"].append("intruder")
        tracker.repair_plans()[0]["state"] = "completed"
        tracker.task_view("original-task")["incidents"][0]["evidence_ids"].clear()
        tracker.task_queue()["tasks"].clear()
        self.assertEqual(export_state(tracker), before)

    def test_queries_validate_identifiers_and_do_not_create_missing_objects(self):
        tracker = RelationshipTracker()
        before = export_state(tracker)
        for invalid in (None, "", " ", 42, True):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                tracker.task_view(invalid)
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                tracker.repair_plan(invalid)
        for invalid in ("", " ", 42, True):
            with self.assertRaises(ValueError):
                tracker.repair_plans(task_id=invalid)
            with self.assertRaises(ValueError):
                tracker.repair_plans(incident_id=invalid)
        self.assertEqual(tracker.task_view("missing")["recovery_status"], "no_evidence")
        self.assertEqual(tracker.task_queue(), {"tasks": [], "unlinked_incidents": []})
        self.assertEqual(tracker.repair_plans(), [])
        self.assertEqual(export_state(tracker), before)


if __name__ == "__main__":
    unittest.main()
