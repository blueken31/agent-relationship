"""Run with python -m examples.evaluation_session from the checkout.

Synthetic admission/receipt checks only. No harness or model is invoked.
"""

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from evaluation.harness import load_json, sdk_envelope, score_pair
from evaluation.session import EvaluationSession, SessionBlocked, prepare_plan, plan_summary


def main():
    root = Path(__file__).resolve().parents[1]
    dataset = load_json(root / "evaluation/cases.json")
    prompt = (root / "evaluation/decision_prompt.txt").read_bytes()
    hooks = b"offline/no-hooks"
    spec = {
        "schema_version": 1,
        "experiment": {
            "experiment_id": "offline-session-demo", "harness": "synthetic/no-harness", "model": "none",
            "prompt_sha256": hashlib.sha256(prompt).hexdigest(),
            "hooks_sha256": hashlib.sha256(hooks).hexdigest(), "replay_mode": "prefix_isolated", "seed": 0,
            "budget": {"max_input_tokens": 0, "max_output_tokens": 0, "max_cost": 0, "currency": "USD"},
        },
        "sdk_revision": "demo/no-sdk-inference",
        "provenance": {arm: {"kind": "synthetic", "run_id": "demo-" + arm,
                             "source_ref": "examples/evaluation_session.py"} for arm in ("native", "sdk")},
        "total_budget": {"max_input_tokens": 0, "max_output_tokens": 0, "max_cost": 0, "currency": "USD"},
    }
    plan = prepare_plan(dataset, spec, prompt_bytes=prompt, hooks_bytes=hooks)
    assert plan_summary(plan)["calls_started"] == 0

    def receipt(request):
        point = request["checkpoint"]
        return {"decision": "investigate", "attribution": "unknown",
                "evidence_ids": [point["observations"][-1]["event_id"]],
                "reason": "Synthetic receipt, no semantic evaluation.",
                "usage": {"input_tokens": 0, "output_tokens": 0, "cost": 0, "currency": "USD"},
                "sdk_context": sdk_envelope(point, {"synthetic_receipt": True}) if request["arm"] == "sdk" else None}

    with TemporaryDirectory(prefix="evaluation-session-") as temporary:
        path = Path(temporary) / "run.sqlite3"
        with EvaluationSession.create(path, plan) as session:
            request = session.claim_next()
            # Simulate losing the local process after the host has a result.
            host_receipt = receipt(request)
        with EvaluationSession(path) as session:
            try:
                session.claim_next()
            except SessionBlocked:
                pass
            else:
                raise AssertionError("uncertain request was automatically retried")
            session.record_result(request["job_id"], host_receipt)
            session.record_result(request["job_id"], host_receipt)  # Idempotent readmission of receipt.
            while session.run_next(receipt) is not None:
                pass
            runs = session.export_runs()
            report = score_pair(dataset, runs["native"], runs["sdk"])
            assert report["arms"]["native"]["coverage"] == 1
            assert session.status()["completed_jobs"] == 38
            # Do not publish arithmetic fixture scores as model results.
            print(json.dumps({"mode": "synthetic_session_check", "completed_jobs": 38,
                              "resume_requires_receipt": True, "provider_calls": 0,
                              "semantic_performance_measured": False}))


if __name__ == "__main__":
    main()
