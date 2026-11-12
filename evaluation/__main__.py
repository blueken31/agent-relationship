"""Run with ``python -m evaluation`` from the repository checkout."""

import argparse
from copy import deepcopy
import json
from pathlib import Path

from .harness import blind_inputs, digest, load_json, score_pair, sdk_envelope, validate_dataset

DEFAULT_CASES = Path(__file__).with_name("cases.json")


def selfcheck(dataset):
    """Test scorer arithmetic with gold-derived synthetic rows, never inference."""
    inputs = blind_inputs(dataset)
    gold = {point["checkpoint_id"]: point["expected"]
            for case in dataset["cases"] for point in case["checkpoints"]}
    experiment = {"experiment_id": "scorer-selfcheck-only", "harness": "synthetic/no-harness",
                  "model": "none", "prompt_sha256": digest("no-prompt"),
                  "hooks_sha256": digest("no-hooks"), "replay_mode": "prefix_isolated",
                  "budget": {"max_input_tokens": 0, "max_output_tokens": 0,
                             "max_cost": None, "currency": None}, "seed": 0}
    runs = []
    for arm in ("native", "sdk"):
        rows = []
        for point in inputs["checkpoints"]:
            expected = gold[point["checkpoint_id"]]
            rows.append({"checkpoint_id": point["checkpoint_id"], "input_sha256": digest(point),
                         "decision": expected["decision"], "attribution": expected["attribution"],
                         "evidence_ids": [point["observations"][-1]["event_id"]],
                         "reason": "Gold copied solely to test scorer arithmetic; no agent ran.",
                         "usage": {"input_tokens": None, "output_tokens": None, "cost": None, "currency": None},
                         "sdk_context": sdk_envelope(point, {"synthetic_selfcheck": True}) if arm == "sdk" else None})
        runs.append({"schema_version": 1, "arm": arm, "input_sha256": digest(inputs),
                     "experiment": deepcopy(experiment), "experiment_sha256": digest(experiment),
                     "sdk_revision": "selfcheck/no-sdk" if arm == "sdk" else None,
                     "provenance": {"kind": "synthetic", "run_id": "selfcheck-" + arm,
                                    "source_ref": "evaluation.__main__.selfcheck; gold copied"},
                     "predictions": rows})
    scored = score_pair(dataset, *runs)
    if any(arm["decision_accuracy"] != 1 for arm in scored["arms"].values()):
        raise AssertionError("scorer does not match copied labels")
    broken = deepcopy(runs[1])
    broken["predictions"].pop()
    try:
        score_pair(dataset, runs[0], broken)
    except ValueError:
        pass
    else:
        raise AssertionError("scorer accepted an incomplete run")
    return {"selfcheck_only": True, "semantic_performance_measured": False,
            "case_count": len(dataset["cases"]), "checkpoint_count": len(gold),
            "checks": ["gold-derived arithmetic", "incomplete run rejected"],
            "note": "No harness, model or SDK diagnosis was run. No performance result is produced."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_CASES)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate", help="validate authored cases")
    export = commands.add_parser("export", help="export checkpoint inputs without gold or future evidence")
    export.add_argument("--out", type=Path)
    score = commands.add_parser("score", help="score complete paired fixed-history runs")
    score.add_argument("--native", type=Path, required=True)
    score.add_argument("--sdk", type=Path, required=True)
    score.add_argument("--out", type=Path)
    commands.add_parser("selfcheck", help="test scorer plumbing only; not model or SDK quality")
    args = parser.parse_args(argv)
    try:
        dataset = validate_dataset(load_json(args.dataset))
        if args.command == "validate":
            result = {"valid": True, "dataset_id": dataset["dataset_id"],
                      "cases": len(dataset["cases"]), "checkpoints": len(blind_inputs(dataset)["checkpoints"])}
        elif args.command == "export":
            result = blind_inputs(dataset)
        elif args.command == "score":
            result = score_pair(dataset, load_json(args.native), load_json(args.sdk))
        else:
            result = selfcheck(dataset)
        output = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if getattr(args, "out", None):
            args.out.write_text(output, encoding="utf-8")
        else:
            print(output, end="")
    except (ValueError, OSError) as error:
        parser.exit(2, f"evaluation: {error}\n")


if __name__ == "__main__":
    main()
