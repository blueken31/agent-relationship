"""Reviewable paired runs with durable reservations, never a provider executor.

The host enforces the supplied per-checkpoint limits at its provider boundary.
This journal bounds admitted work and prevents automatic uncertain-outcome replay;
it cannot control calls made outside that host contract.
"""

from contextlib import contextmanager
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3

from .harness import (
    _object, _validate_experiment, _validate_run, blind_inputs, canonical,
    digest, validate_run,
)

APPLICATION_ID = 0x41524556
LIMIT_FIELDS = {"max_input_tokens", "max_output_tokens", "max_cost", "currency"}
RESULT_FIELDS = {"decision", "attribution", "evidence_ids", "reason", "usage", "sdk_context"}


class SessionBlocked(ValueError):
    """No new work may start until the existing outcome is reconciled."""


def _bytes_hash(value):
    if not isinstance(value, bytes):
        raise ValueError("prompt and hooks must be exact bytes")
    return hashlib.sha256(value).hexdigest()


def _cost(value):
    return Decimal(str(value))


def _cost_float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("aggregate cost exceeds supported finite range")
    return result


def _run(plan, arm, predictions):
    return {
        "schema_version": 1, "arm": arm,
        "input_sha256": digest(plan["inputs"]),
        "experiment": deepcopy(plan["spec"]["experiment"]),
        "experiment_sha256": digest(plan["spec"]["experiment"]),
        "sdk_revision": plan["spec"]["sdk_revision"] if arm == "sdk" else None,
        "provenance": deepcopy(plan["spec"]["provenance"][arm]),
        "predictions": deepcopy(predictions),
    }


def _validate_spec(spec):
    _object(spec, ("schema_version", "experiment", "sdk_revision", "provenance", "total_budget"), "session spec")
    if type(spec["schema_version"]) is not int or spec["schema_version"] != 1:
        raise ValueError("unsupported session spec")
    _validate_experiment(spec["experiment"])
    _object(spec["total_budget"], LIMIT_FIELDS, "total_budget")
    # Reuse the exact token/currency/finite-number checks used by the scorer.
    _validate_experiment({**spec["experiment"], "budget": spec["total_budget"]})
    _object(spec["provenance"], ("native", "sdk"), "provenance")
    for arm in ("native", "sdk"):
        probe = {"inputs": {"checkpoints": []}, "spec": spec}
        _validate_run(_run(probe, arm, []), probe["inputs"], allow_partial=True)
    native, sdk = spec["provenance"]["native"], spec["provenance"]["sdk"]
    if native["kind"] != sdk["kind"] or native["run_id"] == sdk["run_id"]:
        raise ValueError("paired provenance kinds must match and run IDs must differ")
    per, total = spec["experiment"]["budget"], spec["total_budget"]
    if native["kind"] == "captured" and (per["max_cost"] is None or total["max_cost"] is None):
        raise ValueError("captured sessions require explicit per-checkpoint and total cost ceilings")
    if total["max_cost"] is not None:
        if per["max_cost"] is None or per["currency"] != total["currency"]:
            raise ValueError("total cost ceiling requires matching per-checkpoint currency and ceiling")
    for key in ("max_input_tokens", "max_output_tokens", "max_cost"):
        if total[key] is not None and per[key] is not None and _cost(per[key]) > _cost(total[key]):
            raise ValueError("total budget must admit at least one checkpoint")
    canonical(spec)


def prepare_plan(dataset, spec, *, prompt_bytes, hooks_bytes):
    """Freeze blind inputs and configuration without calling a policy or model."""
    _validate_spec(spec)
    if _bytes_hash(prompt_bytes) != spec["experiment"]["prompt_sha256"]:
        raise ValueError("decision prompt bytes do not match the declared digest")
    if _bytes_hash(hooks_bytes) != spec["experiment"]["hooks_sha256"]:
        raise ValueError("hook configuration bytes do not match the declared digest")
    inputs = blind_inputs(dataset)
    body = {"schema_version": 1, "inputs": inputs, "spec": deepcopy(spec)}
    return {**body, "plan_sha256": digest(body)}


def _validate_plan(plan):
    _object(plan, ("schema_version", "inputs", "spec", "plan_sha256"), "plan")
    if type(plan["schema_version"]) is not int or plan["schema_version"] != 1:
        raise ValueError("unsupported plan schema")
    if plan["plan_sha256"] != digest({k: v for k, v in plan.items() if k != "plan_sha256"}):
        raise ValueError("plan digest mismatch")
    _validate_spec(plan["spec"])
    inputs = plan["inputs"]
    _object(inputs, ("schema_version", "dataset_id", "origin", "checkpoints"), "blind inputs")
    if type(inputs["schema_version"]) is not int or inputs["schema_version"] != 1:
        raise ValueError("unsupported blind input schema")
    if not isinstance(inputs["checkpoints"], list) or not inputs["checkpoints"]:
        raise ValueError("plan requires blind checkpoints")
    # Validate each blind checkpoint through the authored dataset contract, with
    # disposable labels that never leave this function or enter a model input.
    identifiers = set()
    for point in inputs["checkpoints"]:
        _object(point, ("case_id", "checkpoint_id", "task", "agents", "dependencies", "observations"), "blind checkpoint")
        cid = point["checkpoint_id"]
        if not isinstance(cid, str) or cid in identifiers:
            raise ValueError("invalid or duplicate checkpoint identity")
        identifiers.add(cid)
        if not isinstance(point["observations"], list) or not point["observations"]:
            raise ValueError("checkpoint requires observations")
        last = point["observations"][-1]
        if not isinstance(last, dict) or "event_id" not in last:
            raise ValueError("invalid observation")
        blind_inputs({"schema_version": 1, "dataset_id": inputs["dataset_id"],
                      "origin": inputs["origin"], "cases": [{
                          "case_id": point["case_id"], "title": "Schema validation only",
                          "task": point["task"], "agents": point["agents"],
                          "dependencies": point["dependencies"], "observations": point["observations"],
                          "checkpoints": [{"checkpoint_id": cid, "through_event_id": last["event_id"],
                                           "expected": {"decision": "investigate", "attribution": "unknown",
                                                        "rationale": "Schema validation only"}}],
                      }]})


def _jobs(plan):
    jobs = []
    for index, point in enumerate(plan["inputs"]["checkpoints"]):
        # Alternate which arm starts each paired checkpoint to limit order bias.
        for arm in (("native", "sdk") if index % 2 == 0 else ("sdk", "native")):
            jobs.append({"job_id": digest([plan["plan_sha256"], arm, point["checkpoint_id"]]),
                         "arm": arm, "checkpoint_id": point["checkpoint_id"],
                         "state": "pending", "result": None, "error_code": None})
    return jobs


def plan_summary(plan):
    """Return a small review card; no gold, provider credentials or raw evidence."""
    _validate_plan(plan)
    count = len(plan["inputs"]["checkpoints"]) * 2
    per, total = plan["spec"]["experiment"]["budget"], plan["spec"]["total_budget"]
    worst = {k: (None if per[k] is None else _cost_float(_cost(per[k]) * count)
                 if k == "max_cost" else per[k] * count)
             for k in ("max_input_tokens", "max_output_tokens", "max_cost")}
    return {"plan_sha256": plan["plan_sha256"], "checkpoint_count": count // 2,
            "paired_jobs": count, "harness": plan["spec"]["experiment"]["harness"],
            "model": plan["spec"]["experiment"]["model"], "per_checkpoint_budget": deepcopy(per),
            "total_budget": deepcopy(total), "worst_case_reservations": worst,
            "fully_funded_at_declared_maxima": all(
                total[k] is None or (worst[k] is not None and _cost(worst[k]) <= _cost(total[k]))
                for k in worst),
            "calls_started": 0, "provider_budget_enforcement": "host_required"}


class EvaluationSession:
    """Single-machine journal for an explicitly prepared experiment.

    Claims reserve a checkpoint's full declared limits before returning input.
    Successful measured receipts settle that reservation. Ambiguous callbacks,
    absent usage and invalid results block further work, including after restart.
    Only an authoritative host receipt can reconcile the existing claim; this
    class never retries it or treats a timeout as an unspent call.
    """

    @classmethod
    def create(cls, path, plan):
        _validate_plan(plan)
        path = Path(path)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        connection = sqlite3.connect(str(path), isolation_level=None)
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
            connection.execute("PRAGMA user_version=1")
            connection.execute("CREATE TABLE journal (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL, checksum TEXT NOT NULL)")
            state = {"plan": deepcopy(plan), "jobs": _jobs(plan)}
            connection.execute("INSERT INTO journal VALUES (1, ?, ?)", (canonical(state), digest(state)))
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
        return cls(path)

    def __init__(self, path):
        uri = Path(path).resolve().as_uri() + "?mode=rw"
        self.connection = sqlite3.connect(uri, uri=True, isolation_level=None, timeout=5)
        try:
            if (self.connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
                    or self.connection.execute("PRAGMA user_version").fetchone()[0] != 1):
                raise ValueError("not a supported evaluation journal")
            self._read()
        except BaseException:
            self.connection.close()
            raise

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _read(self):
        rows = self.connection.execute("SELECT body, checksum FROM journal WHERE id=1").fetchall()
        if len(rows) != 1:
            raise ValueError("missing evaluation checkpoint")
        state = json.loads(rows[0][0])
        if digest(state) != rows[0][1]:
            raise ValueError("evaluation checkpoint checksum mismatch")
        _object(state, ("plan", "jobs"), "journal")
        _validate_plan(state["plan"])
        expected = _jobs(state["plan"])
        if not isinstance(state["jobs"], list) or len(state["jobs"]) != len(expected):
            raise ValueError("invalid journal jobs")
        for job, original in zip(state["jobs"], expected):
            _object(job, original.keys(), "job")
            for key in ("job_id", "arm", "checkpoint_id"):
                if job[key] != original[key]:
                    raise ValueError("changed job identity")
            if not isinstance(job["state"], str) or job["state"] not in ("pending", "reserved", "blocked", "completed"):
                raise ValueError("invalid job state")
            if job["state"] == "completed":
                self._prediction(state["plan"], job, job["result"])
                if job["error_code"] is not None:
                    raise ValueError("completed job has an error")
            elif job["result"] is not None:
                raise ValueError("uncompleted job has a result")
            if job["state"] in ("pending", "reserved") and job["error_code"] is not None:
                raise ValueError("unexpected job error")
            if job["state"] == "blocked" and job["error_code"] not in ("callback_failed", "invalid_result"):
                raise ValueError("invalid job error")
        if sum(job["state"] in ("reserved", "blocked") for job in state["jobs"]) > 1:
            raise ValueError("multiple unresolved claims")
        currencies = {job["result"]["usage"]["currency"] for job in state["jobs"]
                      if job["state"] == "completed" and job["result"]["usage"]["cost"] is not None}
        if len(currencies) > 1:
            raise ValueError("journal cannot aggregate mixed currencies")
        return state

    @contextmanager
    def _transaction(self):
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            state = self._read()
            yield state
            self.connection.execute("UPDATE journal SET body=?, checksum=? WHERE id=1", (canonical(state), digest(state)))
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    @staticmethod
    def _prediction(plan, job, output):
        _object(output, RESULT_FIELDS, "policy result")
        point = next(p for p in plan["inputs"]["checkpoints"] if p["checkpoint_id"] == job["checkpoint_id"])
        prediction = {"checkpoint_id": job["checkpoint_id"], "input_sha256": digest(point), **deepcopy(output)}
        run = _run(plan, job["arm"], [prediction])
        _validate_run(run, plan["inputs"], allow_partial=True)
        usage = output["usage"]
        per = plan["spec"]["experiment"]["budget"]
        if any(usage[k] is None for k in ("input_tokens", "output_tokens")):
            raise ValueError("metered session results require token receipts")
        if per["max_cost"] is not None and usage["cost"] is None:
            raise ValueError("cost-limited session requires a cost receipt")
        return prediction

    @staticmethod
    def _account(state):
        per = state["plan"]["spec"]["experiment"]["budget"]
        result = {"input_tokens": 0, "output_tokens": 0, "cost": Decimal(0)}
        known = dict(result)
        cost_known = True
        for job in state["jobs"]:
            if job["state"] == "pending":
                continue
            if job["state"] == "completed":
                usage = job["result"]["usage"]
                for key in result:
                    if usage[key] is None:
                        cost_known = False
                    else:
                        value = _cost(usage[key]) if key == "cost" else usage[key]
                        result[key] += value
                        known[key] += value
            else:
                for key in result:
                    limit = per["max_" + key]
                    if limit is not None:
                        result[key] += _cost(limit) if key == "cost" else limit
                    else:
                        cost_known = False
        return result, known, cost_known

    def status(self):
        state = self._read()
        blocked = [job for job in state["jobs"] if job["state"] in ("reserved", "blocked")]
        pending = [job for job in state["jobs"] if job["state"] == "pending"]
        used, known, cost_known = self._account(state)
        spec = state["plan"]["spec"]
        per, total = spec["experiment"]["budget"], spec["total_budget"]
        budget_exhausted = any(
            total["max_" + key] is not None
            and _cost(used[key]) + _cost(per["max_" + key]) > _cost(total["max_" + key])
            for key in used)
        return {"plan_sha256": state["plan"]["plan_sha256"],
                "state": ("needs_reconciliation" if blocked else "completed" if not pending
                          else "budget_exhausted" if budget_exhausted else "ready"),
                "completed_jobs": sum(job["state"] == "completed" for job in state["jobs"]),
                "total_jobs": len(state["jobs"]),
                "blocked_jobs": [{k: job[k] for k in ("job_id", "arm", "checkpoint_id", "error_code")} for job in blocked],
                "next_job": ({k: pending[0][k] for k in ("job_id", "arm", "checkpoint_id")}
                             if pending and not blocked and not budget_exhausted else None),
                "charged_or_reserved": {**used, "cost": _cost_float(used["cost"]) if cost_known else None},
                "measured_usage": {**known, "cost": _cost_float(known["cost"]) if cost_known and not blocked else None},
                "total_budget": deepcopy(state["plan"]["spec"]["total_budget"])}

    def claim_next(self):
        """Reserve one job before host work. No automatic expiry or re-claim."""
        with self._transaction() as state:
            if any(job["state"] in ("reserved", "blocked") for job in state["jobs"]):
                raise SessionBlocked("an existing outcome requires host reconciliation")
            job = next((j for j in state["jobs"] if j["state"] == "pending"), None)
            if job is None:
                return None
            used, _, _ = self._account(state)
            spec = state["plan"]["spec"]
            per, total = spec["experiment"]["budget"], spec["total_budget"]
            for key in used:
                ceiling = total["max_" + key]
                if ceiling is not None and _cost(used[key]) + _cost(per["max_" + key]) > _cost(ceiling):
                    raise SessionBlocked("remaining total budget cannot reserve the next checkpoint")
            job["state"] = "reserved"
            return self._request(state["plan"], job)

    @staticmethod
    def _request(plan, job):
        spec = plan["spec"]
        point = next(p for p in plan["inputs"]["checkpoints"] if p["checkpoint_id"] == job["checkpoint_id"])
        return {"job_id": job["job_id"], "arm": job["arm"], "checkpoint": deepcopy(point),
                "budget": deepcopy(spec["experiment"]["budget"]), "plan_sha256": plan["plan_sha256"],
                "experiment": deepcopy(spec["experiment"]),
                "sdk_revision": spec["sdk_revision"] if job["arm"] == "sdk" else None,
                "provenance": deepcopy(spec["provenance"][job["arm"]])}

    def inspect_job(self, job_id):
        """Read the original request/receipt for reconciliation, never re-claim."""
        state = self._read()
        job = next((j for j in state["jobs"] if j["job_id"] == job_id), None)
        if job is None:
            raise ValueError("unknown job")
        return {"state": job["state"], "error_code": job["error_code"],
                "request": self._request(state["plan"], job), "result": deepcopy(job["result"])}

    def completed_results(self):
        """Read retained partial work without presenting it as a scored pair."""
        state = self._read()
        return {"plan_sha256": state["plan"]["plan_sha256"], "scored_pair": False,
                "results": [{"job_id": job["job_id"], "arm": job["arm"],
                             "checkpoint_id": job["checkpoint_id"], "result": deepcopy(job["result"])}
                            for job in state["jobs"] if job["state"] == "completed"]}

    def record_result(self, job_id, output):
        """Settle a reserved job from a host receipt, including after a restart."""
        error = False
        with self._transaction() as state:
            job = next((j for j in state["jobs"] if j["job_id"] == job_id), None)
            if job is None or job["state"] == "pending":
                raise ValueError("result requires an existing claimed job")
            if job["state"] == "completed":
                if canonical(job["result"]) != canonical(output):
                    raise ValueError("completed receipt cannot be replaced")
                return
            try:
                self._prediction(state["plan"], job, output)
                currency = output["usage"]["currency"]
                currencies = {j["result"]["usage"]["currency"] for j in state["jobs"]
                              if j["state"] == "completed" and j["result"]["usage"]["cost"] is not None}
                if currency is not None and currencies and currency not in currencies:
                    raise ValueError("receipt currency differs from previous receipts")
            except (TypeError, ValueError):
                job.update(state="blocked", error_code="invalid_result")
                error = True
            else:
                job.update(state="completed", result=deepcopy(output), error_code=None)
        if error:
            raise SessionBlocked("invalid or unmetered receipt; reservation retained for reconciliation")

    def run_next(self, policy):
        """Call the supplied host function once with a detached reserved request.

        The host selects the requested arm, enforces request['budget'] across
        all assessment/decision calls and returns the usual policy result.
        Exceptions are not persisted or echoed because providers may expose
        credentials or prompt data in their messages.
        """
        if not callable(policy):
            raise ValueError("policy must be callable")
        request = self.claim_next()
        if request is None:
            return None
        try:
            result = policy(deepcopy(request))
        except Exception:
            with self._transaction() as state:
                job = next(j for j in state["jobs"] if j["job_id"] == request["job_id"])
                if job["state"] != "completed":
                    job.update(state="blocked", error_code="callback_failed")
            raise SessionBlocked("host callback failed; reconcile its outcome before continuing") from None
        self.record_result(request["job_id"], result)
        return request["job_id"]

    def export_runs(self):
        """Return complete scorer-compatible arms; never score partial work."""
        state = self._read()
        if any(job["state"] != "completed" for job in state["jobs"]):
            raise SessionBlocked("both arms must finish before exporting scored runs")
        runs = {}
        for arm in ("native", "sdk"):
            predictions = [self._prediction(state["plan"], job, job["result"])
                           for job in state["jobs"] if job["arm"] == arm]
            runs[arm] = _run(state["plan"], arm, predictions)
            validate_run(runs[arm], state["plan"]["inputs"])
        return runs
