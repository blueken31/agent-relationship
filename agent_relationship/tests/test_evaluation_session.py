"""Durable evaluation work admission and receipts; no provider calls."""

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from threading import Barrier
import unittest

from evaluation.harness import digest, load_json, score_pair, sdk_envelope
from evaluation.session import EvaluationSession, SessionBlocked, plan_summary, prepare_plan

ROOT = Path(__file__).resolve().parents[2]
PROMPT = b"identical decision policy"
HOOKS = b"identical hook configuration"


def spec():
    return {"schema_version": 1,
            "experiment": {"experiment_id": "offline-receipts", "harness": "synthetic/no-harness",
                           "model": "none", "prompt_sha256": hashlib.sha256(PROMPT).hexdigest(),
                           "hooks_sha256": hashlib.sha256(HOOKS).hexdigest(),
                           "replay_mode": "prefix_isolated", "seed": 0,
                           "budget": {"max_input_tokens": 10, "max_output_tokens": 5,
                                      "max_cost": 0.03, "currency": "USD"}},
            "sdk_revision": "fixture-only",
            "provenance": {arm: {"kind": "synthetic", "run_id": "fixture-" + arm,
                                 "source_ref": "offline test"} for arm in ("native", "sdk")},
            "total_budget": {"max_input_tokens": 1000, "max_output_tokens": 500,
                             "max_cost": 3, "currency": "USD"}}


def receipt(request):
    point = request["checkpoint"]
    return {"decision": "investigate", "attribution": "unknown",
            "evidence_ids": [point["observations"][-1]["event_id"]],
            "reason": "Synthetic receipt to test wiring only; no semantic judgment.",
            "usage": {"input_tokens": 2, "output_tokens": 1, "cost": 0.01, "currency": "USD"},
            "sdk_context": sdk_envelope(point, {"test_only": True}) if request["arm"] == "sdk" else None}


class TestEvaluationSession(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "run.sqlite3"
        self.dataset = load_json(ROOT / "evaluation/cases.json")
        self.spec = spec()
        self.plan = self.prepare()

    def prepare(self):
        return prepare_plan(self.dataset, self.spec, prompt_bytes=PROMPT, hooks_bytes=HOOKS)

    def create(self):
        return EvaluationSession.create(self.path, self.plan)

    def test_plan_is_blind_and_bound_to_prompt_hooks_and_shared_config(self):
        summary = plan_summary(self.plan)
        self.assertEqual(summary["paired_jobs"], 38)
        self.assertEqual(summary["calls_started"], 0)
        self.assertTrue(summary["fully_funded_at_declared_maxima"])
        self.assertNotIn('"expected"', json.dumps(self.plan))
        self.assertNotIn('"rationale"', json.dumps(self.plan))
        self.assertNotIn('"title"', json.dumps(self.plan))
        for prompt, hooks in ((b"different", HOOKS), (PROMPT, b"different")):
            with self.assertRaises(ValueError):
                prepare_plan(self.dataset, self.spec, prompt_bytes=prompt, hooks_bytes=hooks)
        altered = deepcopy(self.plan)
        altered["spec"]["experiment"]["model"] = "changed"
        with self.assertRaises(ValueError):
            EvaluationSession.create(self.path, altered)
        self.assertFalse(self.path.exists())

    def test_captured_plan_requires_concrete_cost_ceilings(self):
        for arm in ("native", "sdk"):
            self.spec["provenance"][arm]["kind"] = "captured"
        self.spec["total_budget"].update(max_cost=None, currency=None)
        with self.assertRaises(ValueError):
            self.prepare()

    def test_shared_provenance_and_budget_preflight(self):
        self.spec["provenance"]["sdk"]["run_id"] = self.spec["provenance"]["native"]["run_id"]
        with self.assertRaises(ValueError):
            self.prepare()
        self.spec = spec()
        self.spec["total_budget"]["currency"] = "JPY"
        with self.assertRaises(ValueError):
            self.prepare()

    def test_claim_is_durable_detached_and_cannot_be_claimed_twice(self):
        with self.create() as first:
            request = first.claim_next()
            self.assertEqual(request["experiment"], self.spec["experiment"])
            self.assertEqual(first.status()["state"], "needs_reconciliation")
            self.assertEqual(first.status()["charged_or_reserved"]["input_tokens"], 10)
            request["checkpoint"]["observations"].clear()
            with EvaluationSession(self.path) as second:
                with self.assertRaises(SessionBlocked):
                    second.claim_next()
                self.assertEqual(second.status()["blocked_jobs"][0]["job_id"], request["job_id"])

    def test_successful_receipt_survives_restart_and_is_idempotent(self):
        with self.create() as session:
            request = session.claim_next()
            output = receipt(request)
            session.record_result(request["job_id"], output)
            output["reason"] = "caller mutation"
        with EvaluationSession(self.path) as session:
            session.record_result(request["job_id"], receipt(request))
            self.assertEqual(session.status()["completed_jobs"], 1)
            self.assertEqual(session.status()["charged_or_reserved"]["input_tokens"], 2)
            partial = session.completed_results()
            self.assertFalse(partial["scored_pair"])
            self.assertEqual(partial["results"][0]["result"], receipt(request))
            partial["results"][0]["result"]["reason"] = "mutated"
            self.assertEqual(session.inspect_job(request["job_id"])["result"], receipt(request))
            with self.assertRaises(ValueError):
                session.record_result(request["job_id"], output)
            self.assertNotEqual(session.claim_next()["job_id"], request["job_id"])

    def test_unknown_usage_retains_reservation_and_host_can_reconcile(self):
        with self.create() as session:
            request = session.claim_next()
            output = receipt(request)
            output["usage"].update(cost=None, currency=None)
            with self.assertRaises(SessionBlocked):
                session.record_result(request["job_id"], output)
            self.assertEqual(session.status()["state"], "needs_reconciliation")
            self.assertEqual(session.status()["charged_or_reserved"]["cost"], 0.03)
            with self.assertRaises(SessionBlocked):
                session.claim_next()
            session.record_result(request["job_id"], receipt(request))
            self.assertEqual(session.status()["state"], "ready")

    def test_callback_failure_is_safe_and_never_retried(self):
        calls = []
        def fail(request):
            calls.append(request)
            raise RuntimeError("credential-SENTINEL-do-not-store")
        with self.create() as session:
            with self.assertRaises(SessionBlocked) as error:
                session.run_next(fail)
            self.assertNotIn("SENTINEL", str(error.exception))
        with EvaluationSession(self.path) as resumed:
            with self.assertRaises(SessionBlocked):
                resumed.run_next(fail)
            self.assertEqual(len(calls), 1)
            resumed.record_result(calls[0]["job_id"], receipt(calls[0]))
            self.assertEqual(resumed.status()["completed_jobs"], 1)
        self.assertNotIn(b"SENTINEL", self.path.read_bytes())

    def test_abrupt_process_exit_keeps_claim_for_reconciliation(self):
        with self.create():
            pass
        program = """import os,sys
from evaluation.session import EvaluationSession
with EvaluationSession(sys.argv[1]) as session:
    session.run_next(lambda request: os._exit(17))
"""
        process = subprocess.run([sys.executable, "-c", program, str(self.path)], cwd=ROOT, capture_output=True)
        self.assertEqual(process.returncode, 17)
        with EvaluationSession(self.path) as session:
            self.assertEqual(session.status()["state"], "needs_reconciliation")
            job_id = session.status()["blocked_jobs"][0]["job_id"]
            original = session.inspect_job(job_id)
            self.assertEqual(original["state"], "reserved")
            self.assertIsNone(original["result"])
            with self.assertRaises(SessionBlocked):
                session.claim_next()

    def test_reserve_full_checkpoint_limit_before_another_callback(self):
        self.spec["total_budget"]["max_input_tokens"] = 10
        self.plan = self.prepare()
        self.assertFalse(plan_summary(self.plan)["fully_funded_at_declared_maxima"])
        calls = []
        def policy(request):
            calls.append(request)
            return receipt(request)
        with self.create() as session:
            session.run_next(policy)
            self.assertEqual(session.status()["state"], "budget_exhausted")
            self.assertIsNone(session.status()["next_job"])
            with self.assertRaises(SessionBlocked):
                session.run_next(policy)
        self.assertEqual(len(calls), 1)

    def test_invalid_over_budget_or_future_evidence_blocks_immediately(self):
        for field in ("tokens", "future", "unknown", "nan"):
            with self.subTest(field=field):
                path = Path(self.tmp.name) / (field + ".sqlite3")
                with EvaluationSession.create(path, self.plan) as session:
                    request = session.claim_next()
                    output = receipt(request)
                    if field == "tokens":
                        output["usage"]["input_tokens"] = 11
                    elif field == "future":
                        output["evidence_ids"] = ["foreign-event"]
                    elif field == "unknown":
                        output["usage"]["output_tokens"] = None
                    else:
                        output["usage"]["cost"] = float("nan")
                    with self.assertRaises(SessionBlocked):
                        session.record_result(request["job_id"], output)
                    self.assertEqual(session.status()["completed_jobs"], 0)
                    with self.assertRaises(SessionBlocked):
                        session.export_runs()

    def test_cannot_record_unclaimed_or_unknown_jobs(self):
        with self.create() as session:
            job = session.status()["next_job"]
            with self.assertRaises(ValueError):
                session.record_result(job["job_id"], {})
            with self.assertRaises(ValueError):
                session.record_result("unknown", {})
            self.assertEqual(session.status()["state"], "ready")

    def test_completed_pair_exports_to_existing_scorer(self):
        requests = []
        def policy(request):
            requests.append(request)
            return receipt(request)
        with self.create() as session:
            while session.run_next(policy) is not None:
                pass
            runs = session.export_runs()
            self.assertEqual(session.status()["state"], "completed")
            self.assertEqual(len(requests), 38)
            self.assertEqual([r["arm"] for r in requests[:4]], ["native", "sdk", "sdk", "native"])
            report = score_pair(self.dataset, runs["native"], runs["sdk"])
            self.assertEqual(report["arms"]["native"]["coverage"], 1)
            self.assertFalse(report["live_task_outcomes_measured"])
            self.assertAlmostEqual(session.status()["measured_usage"]["cost"], 0.38)
            self.assertIsNone(session.run_next(policy))

    def test_existing_or_wrong_database_is_not_replaced(self):
        self.path.write_text("unrelated")
        with self.assertRaises(FileExistsError):
            self.create()
        with self.assertRaises(sqlite3.Error):
            EvaluationSession(self.path)
        self.assertEqual(self.path.read_text(), "unrelated")

    def test_checkpoint_corruption_fails_without_reset(self):
        with self.create():
            pass
        with sqlite3.connect(self.path) as connection:
            connection.execute("UPDATE journal SET body='{}'")
        with self.assertRaises(ValueError):
            EvaluationSession(self.path)

    def test_status_cli_does_not_need_gold_dataset(self):
        with self.create():
            pass
        completed = subprocess.run(
            [sys.executable, "-m", "evaluation", "--dataset", "/missing-gold.json",
             "session-status", "--db", str(self.path)], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["state"], "ready")

    def test_concurrent_hosts_admit_only_one_checkpoint(self):
        with self.create():
            pass
        barrier = Barrier(2)
        def claim():
            with EvaluationSession(self.path) as session:
                barrier.wait(timeout=5)
                try:
                    return session.claim_next()["job_id"]
                except SessionBlocked:
                    return None
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: claim(), range(2)))
        self.assertEqual(sum(result is not None for result in results), 1)

    def test_cli_prepare_claim_record_export_matches_scorer(self):
        # One paired checkpoint makes this an interface test, not a benchmark.
        self.dataset["cases"] = [self.dataset["cases"][0]]
        self.dataset["cases"][0]["checkpoints"] = self.dataset["cases"][0]["checkpoints"][:1]
        folder = Path(self.tmp.name)
        for name, data in (("cases.json", self.dataset), ("spec.json", self.spec)):
            (folder / name).write_text(json.dumps(data))
        (folder / "prompt.txt").write_bytes(PROMPT)
        (folder / "hooks.txt").write_bytes(HOOKS)
        def cli(*args):
            process = subprocess.run([sys.executable, "-m", "evaluation", "--dataset", str(folder / "cases.json"), *map(str, args)],
                                     cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(process.returncode, 0, process.stderr)
            return json.loads(process.stdout)
        summary = cli("prepare", "--spec", folder / "spec.json", "--prompt", folder / "prompt.txt",
                      "--hooks", folder / "hooks.txt", "--out", folder / "plan.json")
        self.assertEqual(summary["paired_jobs"], 2)
        cli("session-create", "--plan", folder / "plan.json", "--db", self.path)
        for index in range(2):
            request_path = folder / ("request-" + str(index) + ".json")
            cli("session-claim", "--db", self.path, "--out", request_path)
            request = load_json(request_path)
            result_path = folder / ("receipt-" + str(index) + ".json")
            result_path.write_text(json.dumps(receipt(request)))
            cli("session-record", "--db", self.path, "--job-id", request["job_id"], "--receipt", result_path)
        cli("session-export", "--db", self.path, "--out-dir", folder / "results")
        report = cli("score", "--native", folder / "results/native.json", "--sdk", folder / "results/sdk.json")
        self.assertFalse(report["live_task_outcomes_measured"])
        self.assertEqual(report["arms"]["sdk"]["checkpoints"], 1)


if __name__ == "__main__":
    unittest.main()
