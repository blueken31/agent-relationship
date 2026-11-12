# Collaboration value evaluation

The question is whether adding this SDK improves decisions about multi-agent
collaboration beyond a capable native harness with appropriate instructions,
hooks, memory and review rules. A green test suite cannot answer that question.
This checkout now supplies authored cases, blind input export, a callback runner
and strict paired scoring. No live diagnostic-quality result has been measured.

For a real host run, use the [prepared session workflow](EVALUATION_RUNS.md) to
review exact configuration and limits, retain completed receipts across restarts,
and stop when a previous outcome or its usage is unknown. The existing scorer
remains usable for externally collected artifacts with explicitly unknown usage.

## Scope and evidence level

`evaluation/cases.json` contains **12 synthetic authored scenarios, 31 observations
and 19 checkpoints**. They cover legitimate review rejection; bilateral gain that
violates the shared goal; a shared upstream outage; severe unresolved harm followed
by small successes; missing transcripts; changed identity; changed model; stale or
wrong-task repair evidence; verified repair; third-party harm; and duplicate work.
Each case records agent roles, directed observations, dependencies, the original
goal and a specific proposed next action. These are development cases, neither
representative production samples nor held-out test data. Gold judgments are
reviewable assumptions; they are not facts produced by the SDK.

The first evaluation is **fixed-history decision replay**. Both arms see the same
observed prefix and choose what should happen next. It does not measure actual
pauses, task completion, recovery duration, human interventions, agent execution
or cluster causality. These require a subsequent controlled live task study.

| Decision | Meaning for the stated proposed action |
| --- | --- |
| `continue` | Available evidence permits that bounded next step, subject to the host's own authorization. |
| `investigate` | Obtain missing evidence or resolve an uncertain/shared dependency before that step. This is appropriate abstention in some cases. |
| `hold` | Observed harm or a violated requirement makes that step unsuitable until the issue is addressed. |

Attribution is scored separately. `pair` means the evidence ties the relevant
problem to that pair's coordination or action; it does not establish intent,
equal responsibility or blame for each individual. `shared_dependency` requires
independent evidence for an upstream cause affecting the agents. `unknown` means
the available evidence cannot locate the problem. `none` means no harmful
collaboration is established for the proposed step. A failed tool or an open SDK
`task_failure` incident does not establish agent blame. The SDK's `unknown`/`review` recommendation is not automatically a
`hold` decision. The decision policy must interpret it under the same rubric used
for the native arm.

## Commands

Run from the repository root with Python 3.10 or newer; no extra dependencies,
network, API key or model calls are needed:

```bash
python -m evaluation validate
python -m evaluation export --out /tmp/collaboration-inputs.json
python -m evaluation selfcheck
python -m evaluation score --native /tmp/native.json --sdk /tmp/sdk.json --out /tmp/comparison.json
```

`selfcheck` copies labels solely to test scoring arithmetic and verifies that an
incomplete run is rejected. It reports no diagnostic accuracy and never runs a
harness or SDK diagnosis. Unit tests likewise validate data isolation, pairing,
accounting and rejection behavior, not model performance. `evaluation/` is
checkout tooling and is excluded from the installable SDK package.

The blind export removes titles, expected labels and rationales. Case, task,
event and checkpoint IDs are opaque. Each checkpoint includes the entire available
history and omits later observations. The callback receives a deep copy, so its
mutations cannot modify later inputs or the authored dataset. Do not pass the
original labeled file, scorer output or gold rationales to either model.

## Fair native and augmented arms

Use the same harness/version, final decision model/version, shared decision
prompt, hooks, goal, evidence, seed where supported, execution permissions and
**total per-checkpoint budget**. The native arm may use its normal persistent
state, tools and diagnostic rules, represented fairly in the isolated prefix
replay. Give it the same rubric and a capable prompt; disabling useful native
features would create a weak comparison. The SDK arm additionally gets recorded
SDK observations and task views. Its assessment calls and retries count toward
the same total budget, along with the final decision call.

`evaluation/decision_prompt.txt` supplies a shared, substantive starting policy
for both arms. It covers the complete task history, legitimate dissent, shared
causes, third-party effects, evidence freshness and appropriate abstention. The
model returns only its decision, attribution, evidence IDs and reason; the host
adds measured usage and SDK context. Tune the baseline before freezing the
experiment, not after seeing treatment labels. Hash the exact prompt bytes used
at runtime, including any edits and host instruction wrapper, for example with
`hashlib.sha256(Path("evaluation/decision_prompt.txt").read_bytes()).hexdigest()`.
The template selects no model and authorizes no spending.

The experiment declares SHA-256 hashes of the common prompt and hook configuration.
Use exact content hashes, not arbitrary labels. Record the exact SDK commit and
its assessment backend/model/prompt configuration in the SDK revision/source
manifest; preserve the actual configurations with the capture. Configuration
hash equality is a consistency check, not proof that the executions were equal.

`run_policy()` invokes a caller-owned callback on each blind prefix. A useful
integration is `examples/harness_sdk_policy.py`, which provides `NativePolicy` and
`SDKPolicy` wrappers around the same `decide(blind_checkpoint, sdk_context)`
function. The latter constructs a fresh tracker and explicit assessment backend
for each prefix, records observed facts and supplies read-only task context. It
never converts a score into a permission or an automatic pause. The same complete
history still reaches `decide` in both arms.

```python
from evaluation.harness import load_json, run_policy
from examples.harness_sdk_policy import NativePolicy, SDKPolicy

cases = load_json("evaluation/cases.json")
# Caller supplies experiment, decide, backend_factory and actual usage metering.
# Do not use the case labels to implement any of these callbacks.
native = run_policy(
    cases, "native", experiment, NativePolicy(decide),
    provenance={"kind": "captured", "run_id": "run-a", "source_ref": "capture-a-manifest"},
)
sdk = run_policy(
    cases, "sdk", experiment,
    SDKPolicy(decide, backend_factory, read_sdk_usage),
    provenance={"kind": "captured", "run_id": "run-b", "source_ref": "capture-b-manifest"},
    sdk_revision="exact-commit-and-assessment-config-manifest",
)
```

This is an integration sketch: callbacks and the `experiment` are intentionally
not predefined and no paid call starts automatically. The bridge's executable
example is only an offline wiring check with unknown assessments. It does not
reconstruct recovery plans or host authorizations from narrative claims of repair;
its `recovery_replayed` field is false. Lifecycle recovery semantics continue to
have their separate deterministic tests. A real recovery comparison must ingest
actual structured host actions and receipts before claiming recovery benefits.

## Run artifact contract

`experiment` has exactly these fields:

```json
{
  "experiment_id": "review-001",
  "harness": "provider-and-exact-version",
  "model": "exact-final-decision-model",
  "prompt_sha256": "64 lowercase hex characters",
  "hooks_sha256": "64 lowercase hex characters",
  "replay_mode": "prefix_isolated",
  "budget": {
    "max_input_tokens": 4000,
    "max_output_tokens": 1000,
    "max_cost": null,
    "currency": null
  },
  "seed": null
}
```

The numbers above demonstrate the schema, not an approved budget. The caller
must enforce chosen limits at the provider boundary **before** spending them.
The runner validates metadata before the first callback and rejects each invalid
or reported over-budget row before invoking another callback. It cannot stop an
external call already in progress.
For each isolated prefix, meter all calls for that arm; do not reuse unreported
cached reasoning across checkpoints. If prefix replay work is repeated, report
its actual repeated cost.

The callback returns exactly:

```json
{
  "decision": "investigate",
  "attribution": "unknown",
  "evidence_ids": ["event-001-001"],
  "reason": "Explain the observed evidence and remaining uncertainty.",
  "usage": {"input_tokens": null, "output_tokens": null, "cost": null, "currency": null},
  "sdk_context": null
}
```

For the SDK arm, `sdk_context` is an audit envelope made with
`sdk_envelope(blind_checkpoint, payload)`: the source input digest, unique source
event IDs, payload digest and actual SDK payload. The wrapper supplies it
automatically. Its source IDs must be available in that prefix. Direct reserved
gold fields (`expected`, `gold`, `gold_label`, `rationale`) are rejected recursively
in the payload. This prevents accidental field leakage and inconsistent recorded
context; it cannot prove that hidden model state or arbitrary prose is label-free.

`run_policy()` adds schema version 1, arm, full blind-input SHA-256, shared
experiment and its SHA-256, SDK revision (`null` for native), provenance, and
checkpoint rows with their individual input hashes. Provenance has `kind`
(`synthetic` or `captured`), distinct `run_id`, and `source_ref` for retained
artifacts. `captured` is an operator declaration, not independent attestation
and not a claim of live task execution. The scorer requires matching provenance
kinds across arms and all expected checkpoints exactly once. Empty, partial,
duplicate, foreign or tampered predictions fail instead of becoming successful
missing data. Evidence references must belong to the current prefix.

Unknown token usage or cost is `null`, never zero. Measured zero is allowed.
Cost requires a currency; unknown cost requires null currency. Partial usage
produces a null total, a separately named known subtotal and a measurement count.
Mixed currencies within an arm are rejected. Do not infer the cost of missing
SDK assessment calls from the final decision response alone.

## Reading results and the next gate

Reports include decision match, attribution match, false holds, missed holds,
unsafe continues, investigate count, paired disagreements and measured usage
completeness. `missed_holds` includes `investigate` when the expected decision was
`hold`; `unsafe_continues` separately identifies proceeding despite either a hold
or a need for investigation. Investigating an uncertain case is not itself a
false hold. Coverage must be complete for a scored pair. A positive accuracy
difference on these authored cases is not production uplift or a statistical
significance claim.

Before making an incremental-value claim:

1. Review and freeze the rubric, cases, native baseline, shared prompt/hooks and
   budget before viewing treatment results; add independently authored held-out
   cases and repeated trials.
2. Run both policies with a real assessment backend on identical observed
   prefixes; retain provider/harness versions, redacted captures, usage,
   configurations and any failures. Neither arm receives labels.
3. Review disagreements with evidence, especially legitimate rejection, shared
   upstream failure and stale verification. Report false intervention and missed
   issues alongside abstention and incremental cost.
4. Then run bounded real multi-agent tasks with authoritative host receipts and
   measure completed tasks, actual pauses, duplicate work, recovery time and
   human intervention. Compare within the same permissions and total budget.

The current batch completes evaluation plumbing and a documented integration
boundary. The measured comparison and live task gate remain pending; no claim
that this SDK outperforms native Codex or another harness follows from these tests.
