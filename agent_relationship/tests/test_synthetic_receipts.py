"""Probe safety contracts using synthetic receipts, not live quality estimates."""

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from agent_relationship import RelationshipTracker
from evaluation.synthetic_receipts import (
    BEFORE, FIXED, NAMES, UnavailableAssessor, observe, validate_receipt,
)


def receipt(revision, status):
    stamp = datetime.now(timezone.utc).isoformat()
    return {
        "source_revision": revision, "started_at": stamp, "finished_at": stamp,
        "exit_code": 1 if status == "failed" else 0,
        "test_file_sha256": "a" * 64,
        "junit_sha256": ("b" if status == "failed" else "c") * 64,
        "cases": [{"name": name, "status": status, **(
            {"failure_message": "AssertionError: reference missing"}
            if status == "failed" else {})} for name in NAMES],
    }


class SyntheticReceiptTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "observation.sqlite3"

    def test_passing_host_test_does_not_fabricate_model_recovery(self):
        report = observe(receipt(BEFORE, "failed"),
                         lambda: receipt(FIXED, "passed"), self.path)
        self.assertTrue(report["checks"]["host_regression_recovered"])
        self.assertFalse(report["checks"]["sdk_incidents_resolved"])
        self.assertEqual(report["sdk_provider_calls"], 0)
        self.assertIsNone(report["usage"]["cost"])
        self.assertEqual(len(report["closure_rejections"]), 2)
        self.assertTrue(report["checks"]["foreign_task_rejected"])
        for view in report["after_views"]:
            self.assertEqual(view["recovery_status"], "needs_attention")
            self.assertEqual({i["assessment"]["category"] for i in view["incidents"]},
                             {"task_failure", "evidence_needed"})
            self.assertEqual(view["open_incident_count"], 2)
            self.assertEqual(view["summary"], "宿主复测已通过，协作评估待核对")
            self.assertEqual(view["primary_action"]["kind"], "inspect_assessment")
            self.assertTrue(all(c["handling"]["state"] == "awaiting_assessment"
                                for c in view["incidents"]))
        self.assertTrue(all(not p["grants_permission"] for p in report["plans"]))

    def test_setup_errors_skips_missing_cases_and_other_failures_never_dispatch(self):
        before = receipt(BEFORE, "failed")
        bad_inputs = []
        for status in ("error", "skipped"):
            bad = deepcopy(before)
            bad["cases"][0]["status"] = status
            bad_inputs.append(bad)
        bad = deepcopy(before)
        bad["cases"].pop()
        bad_inputs.append(bad)
        for message in ("Invalid host header", ""):
            bad = deepcopy(before)
            bad["cases"][0]["failure_message"] = message
            bad_inputs.append(bad)
        for bad in bad_inputs:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    observe(bad, lambda: self.fail("must not dispatch"), self.path)
                self.assertFalse(self.path.exists())

    def test_historical_pass_cannot_be_recorded_as_fresh_verification(self):
        old_pass = receipt(FIXED, "passed")
        with self.assertRaisesRegex(ValueError, "predates"):
            observe(receipt(BEFORE, "failed"), lambda: old_pass, self.path)

    def test_existing_run_is_not_reexecuted(self):
        self.path.touch()
        with self.assertRaises(FileExistsError):
            observe(receipt(BEFORE, "failed"),
                    lambda: self.fail("must not redispatch"), self.path)

    def test_different_test_file_is_not_a_valid_repair_comparison(self):
        def verify():
            after = receipt(FIXED, "passed")
            after["test_file_sha256"] = "d" * 64
            return after
        with self.assertRaisesRegex(ValueError, "same test file"):
            observe(receipt(BEFORE, "failed"), verify, self.path)

    def test_callback_failure_keeps_started_plans_and_no_automatic_retry(self):
        calls = []
        def fail():
            calls.append(True)
            raise RuntimeError("host interrupted")
        with self.assertRaisesRegex(RuntimeError, "host interrupted"):
            observe(receipt(BEFORE, "failed"), fail, self.path)
        self.assertEqual(len(calls), 1)
        with RelationshipTracker(llm=UnavailableAssessor(), storage_path=self.path) as tracker:
            for suffix in ("false", "true"):
                view = tracker.task_view("fixture:document-mode=" + suffix)
                self.assertEqual(view["active_plan_count"], 1)
                self.assertEqual(view["open_incident_count"], 1)
        with self.assertRaises(FileExistsError):
            observe(receipt(BEFORE, "failed"), fail, self.path)
        self.assertEqual(len(calls), 1)
