"""Evaluation integrity checks, not evidence of diagnostic model quality."""

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from evaluation.harness import (
    blind_inputs, digest, load_json, run_policy, score_pair, sdk_envelope, validate_dataset, validate_run,
)

ROOT = Path(__file__).resolve().parents[2]


class TestCollaborationEvaluation(unittest.TestCase):
    def setUp(self):
        self.dataset = load_json(ROOT / "evaluation" / "cases.json")
        self.inputs = blind_inputs(self.dataset)
        self.experiment = {
            "experiment_id": "test-only", "harness": "test/no-live-harness", "model": "none",
            "prompt_sha256": digest("same complete policy"), "hooks_sha256": digest("same hooks"),
            "replay_mode": "prefix_isolated", "budget": {
                "max_input_tokens": 1000, "max_output_tokens": 100,
                "max_cost": 1, "currency": "USD"}, "seed": 7,
        }

    @staticmethod
    def policy(point):
        return {"decision": "investigate", "attribution": "unknown",
                "evidence_ids": [point["observations"][-1]["event_id"]],
                "reason": "Synthetic integrity test, not semantic inference.",
                "usage": {"input_tokens": None, "output_tokens": None, "cost": None, "currency": None},
                "sdk_context": None}

    def make_run(self, arm):
        def callback(point):
            output = self.policy(point)
            if arm == "sdk":
                output["sdk_context"] = sdk_envelope(point, {"test_only": True})
            return output
        return run_policy(self.dataset, arm, self.experiment, callback,
                          provenance={"kind": "synthetic", "run_id": arm + "-test", "source_ref": "unit-test"},
                          sdk_revision="test-only" if arm == "sdk" else None)

    def pair(self):
        return self.make_run("native"), self.make_run("sdk")

    def test_export_omits_gold_and_future_observations(self):
        encoded = json.dumps(self.inputs)
        for key in ('"expected"', '"rationale"', '"title"', '"through_event_id"'):
            self.assertNotIn(key, encoded)
        case = next(case for case in self.dataset["cases"] if len(case["checkpoints"]) > 1)
        first = next(point for point in self.inputs["checkpoints"] if point["case_id"] == case["case_id"])
        self.assertEqual(first["observations"][-1]["event_id"], case["checkpoints"][0]["through_event_id"])
        future = case["observations"][-1]["event_id"]
        self.assertNotIn(future, {event["event_id"] for event in first["observations"]})
        self.assertEqual(first["task"], case["task"])
        self.assertEqual(first["dependencies"], case["dependencies"])

    def test_callback_mutation_does_not_change_dataset_or_other_prefixes(self):
        before = deepcopy(self.dataset)
        observed = []
        def callback(point):
            self.assertNotIn("expected", point)
            self.assertNotIn("checkpoints", point)
            output = self.policy(point)
            observed.append(point["task"]["goal"])
            point["task"]["goal"] = "modified"
            point["observations"].clear()
            return output
        run_policy(self.dataset, "native", self.experiment, callback,
                   provenance={"kind": "synthetic", "run_id": "isolation", "source_ref": "test"})
        self.assertEqual(before, self.dataset)
        self.assertNotIn("modified", observed)

    def test_unknown_usage_is_not_zero_total(self):
        native, sdk = self.pair()
        native["predictions"][0]["usage"] = {"input_tokens": 12, "output_tokens": 3, "cost": .1, "currency": "USD"}
        report = score_pair(self.dataset, native, sdk)
        usage = report["arms"]["native"]["usage"]
        self.assertIsNone(usage["input_tokens"]["total"])
        self.assertEqual(usage["input_tokens"]["known_subtotal"], 12)
        self.assertEqual(usage["input_tokens"]["measured_checkpoints"], 1)
        self.assertFalse(usage["input_tokens"]["complete"])
        self.assertIsNone(usage["cost"]["total"])
        self.assertFalse(report["live_task_outcomes_measured"])

    def test_complete_usage_is_summed(self):
        native, sdk = self.pair()
        for row in sdk["predictions"]:
            row["usage"] = {"input_tokens": 12, "output_tokens": 3, "cost": .1, "currency": "USD"}
        report = score_pair(self.dataset, native, sdk)
        self.assertEqual(report["arms"]["sdk"]["usage"]["input_tokens"]["total"], len(sdk["predictions"]) * 12)
        self.assertTrue(report["arms"]["sdk"]["usage"]["cost"]["complete"])

    def test_missing_duplicate_and_foreign_predictions_are_rejected(self):
        for mutation in (lambda rows: rows.pop(), lambda rows: rows.append(deepcopy(rows[0])),
                         lambda rows: rows[0].update(checkpoint_id="foreign")):
            with self.subTest(mutation=mutation):
                native, sdk = self.pair()
                mutation(sdk["predictions"])
                with self.assertRaises(ValueError):
                    score_pair(self.dataset, native, sdk)

    def test_future_foreign_duplicate_evidence_is_rejected(self):
        run = self.make_run("native")
        first = run["predictions"][0]
        available = self.inputs["checkpoints"][0]["observations"][0]["event_id"]
        for invalid in (["future-event"], [available, available], [], [42]):
            with self.subTest(invalid=invalid):
                altered = deepcopy(run)
                altered["predictions"][0]["evidence_ids"] = invalid
                with self.assertRaises(ValueError):
                    validate_run(altered, self.inputs)

    def test_mismatched_experiment_or_evidence_digest_is_rejected(self):
        for kind in ("model", "prompt", "budget", "seed", "inputs", "checkpoint"):
            with self.subTest(kind=kind):
                native, sdk = self.pair()
                if kind == "model":
                    sdk["experiment"]["model"] = "different"
                elif kind == "prompt":
                    sdk["experiment"]["prompt_sha256"] = digest("different prompt")
                elif kind == "budget":
                    sdk["experiment"]["budget"]["max_output_tokens"] += 1
                elif kind == "seed":
                    sdk["experiment"]["seed"] += 1
                elif kind == "inputs":
                    sdk["input_sha256"] = digest("different input")
                else:
                    sdk["predictions"][0]["input_sha256"] = digest("different checkpoint")
                sdk["experiment_sha256"] = digest(sdk["experiment"])
                with self.assertRaises(ValueError):
                    score_pair(self.dataset, native, sdk)

    def test_investigate_is_not_false_hold_but_does_not_mask_missed_hold(self):
        report = score_pair(self.dataset, *self.pair())
        expected_hold = sum(point["expected"]["decision"] == "hold"
                            for case in self.dataset["cases"] for point in case["checkpoints"])
        metrics = report["arms"]["native"]
        self.assertEqual(metrics["false_holds"], 0)
        self.assertEqual(metrics["unsafe_continues"], 0)
        self.assertEqual(metrics["missed_holds"], expected_hold)
        self.assertEqual(metrics["investigate_decisions"], len(self.inputs["checkpoints"]))

    def test_synthetic_and_captured_cannot_be_mixed(self):
        native, sdk = self.pair()
        sdk["provenance"]["kind"] = "captured"
        with self.assertRaises(ValueError):
            score_pair(self.dataset, native, sdk)

    def test_arm_context_and_revision_are_explicit(self):
        for arm, key, value in (("native", "sdk_revision", "SDK"),
                                ("sdk", "sdk_revision", None)):
            with self.subTest(arm=arm):
                run = self.make_run(arm)
                run[key] = value
                with self.assertRaises(ValueError):
                    validate_run(run, self.inputs)
        native = self.make_run("native")
        native["predictions"][0]["sdk_context"] = {}
        with self.assertRaises(ValueError):
            validate_run(native, self.inputs)

    def test_costs_budgets_and_json_numbers_are_strict(self):
        cases = [("input_tokens", True), ("input_tokens", -1), ("input_tokens", 1001),
                 ("output_tokens", 1.5), ("cost", float("nan")), ("cost", float("inf")),
                 ("cost", 1.01)]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                run = self.make_run("sdk")
                run["predictions"][0]["usage"][field] = value
                if field == "cost":
                    run["predictions"][0]["usage"]["currency"] = "USD"
                with self.assertRaises(ValueError):
                    validate_run(run, self.inputs)

    def test_json_duplicate_keys_and_nonfinite_values_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            for text in ('{"a":1,"a":2}', '{"a":NaN}'):
                path.write_text(text, encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_json(path)

    def test_dataset_bad_references_and_extra_gold_fields_are_rejected(self):
        for mutation in (
            lambda case: case["observations"][0].update(actor="unknown"),
            lambda case: case["checkpoints"][0].update(through_event_id="other-case"),
            lambda case: case["observations"][0].update(expected="hold"),
        ):
            dataset = deepcopy(self.dataset)
            mutation(dataset["cases"][0])
            with self.assertRaises(ValueError):
                validate_dataset(dataset)

    def test_invalid_metadata_prevents_all_callbacks(self):
        calls = []
        for arm, provenance, revision in (
            ("sdk", {}, None),
            ("sdk", {"kind": "synthetic", "run_id": "x", "source_ref": "test"}, None),
            ([], {"kind": "synthetic", "run_id": "x", "source_ref": "test"}, None),
        ):
            with self.subTest(arm=arm):
                with self.assertRaises(ValueError):
                    run_policy(self.dataset, arm, self.experiment,
                               lambda point: calls.append(point),
                               provenance=provenance, sdk_revision=revision)
        self.assertEqual(calls, [])

    def test_invalid_or_overbudget_first_row_stops_further_callbacks(self):
        for overbudget in (False, True):
            calls = []
            def callback(point):
                calls.append(point)
                result = self.policy(point)
                if overbudget:
                    result["usage"]["input_tokens"] = 1001
                else:
                    result["decision"] = "invalid"
                return result
            with self.subTest(overbudget=overbudget):
                with self.assertRaises(ValueError):
                    run_policy(self.dataset, "native", self.experiment, callback,
                               provenance={"kind": "synthetic", "run_id": "x", "source_ref": "test"})
                self.assertEqual(len(calls), 1)

    def test_malformed_unhashable_schema_fields_raise_value_error(self):
        dataset = deepcopy(self.dataset)
        dataset["cases"][0]["dependencies"] = [{"source": {}, "target": "x", "resource": "x"}]
        with self.assertRaises(ValueError):
            validate_dataset(dataset)
        dataset = deepcopy(self.dataset)
        dataset["cases"][0]["checkpoints"][0]["through_event_id"] = []
        with self.assertRaises(ValueError):
            validate_dataset(dataset)
        run = self.make_run("native")
        run["arm"] = []
        with self.assertRaises(ValueError):
            validate_run(run, self.inputs)
        run = self.make_run("native")
        run["predictions"][0]["usage"]["input_tokens"] = 10 ** 1000
        with self.assertRaises(ValueError):
            validate_run(run, self.inputs)

    def test_sdk_context_is_bound_to_prefix_and_rejects_gold(self):
        for kind in ("input", "payload", "future", "gold"):
            with self.subTest(kind=kind):
                run = self.make_run("sdk")
                context = run["predictions"][0]["sdk_context"]
                if kind == "input":
                    context["source_input_sha256"] = digest("other checkpoint")
                elif kind == "payload":
                    context["payload"]["tampered"] = True
                elif kind == "future":
                    context["source_event_ids"].append("future-event")
                else:
                    context["payload"] = {"nested": [{"expected": "hold"}]}
                    context["payload_sha256"] = digest(context["payload"])
                with self.assertRaises(ValueError):
                    validate_run(run, self.inputs)

    def test_blind_ids_are_opaque_and_cannot_name_case_labels(self):
        for point in self.inputs["checkpoints"]:
            self.assertRegex(point["case_id"], r"^case-\d{3}$")
            self.assertRegex(point["checkpoint_id"], r"^checkpoint-\d{3}-\d{3}$")
            self.assertRegex(point["task"]["task_id"], r"^task-\d{3}$")
            for event in point["observations"]:
                self.assertRegex(event["event_id"], r"^event-\d{3}-\d{3}$")

    def test_cli_selfcheck_never_outputs_model_performance(self):
        process = subprocess.run([sys.executable, "-m", "evaluation", "selfcheck"],
                                 cwd=ROOT, capture_output=True, text=True, check=True)
        output = json.loads(process.stdout)
        self.assertTrue(output["selfcheck_only"])
        self.assertFalse(output["semantic_performance_measured"])
        self.assertNotIn("decision_accuracy", output)

    def test_cli_export_and_invalid_score_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inputs.json"
            subprocess.run([sys.executable, "-m", "evaluation", "export", "--out", str(path)],
                           cwd=ROOT, capture_output=True, text=True, check=True)
            self.assertEqual(load_json(path), self.inputs)
            process = subprocess.run([sys.executable, "-m", "evaluation", "score", "--native", str(path), "--sdk", str(path)],
                                     cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(process.returncode, 2)
            self.assertIn("evaluation:", process.stderr)
