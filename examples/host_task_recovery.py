"""Reference host handoff: reconnect to one original task step after recovery.

Run from the repository root:
    PYTHONPATH=. python examples/host_task_recovery.py

This host performs a SIMULATED LOCAL effect: updating its own task row and
writing the receipt in the SAME SQLite transaction. It does not execute an
agent, call a model or contact an external service. The separate SDK database
contains diagnostic evidence, not execution permission.

The SDK readiness read and host commit are not one atomic transaction. A real
host must serialize its task lifecycle, recheck its policy and manage concurrent
new evidence. This example rechecks the current task view immediately before
its local effect; it does not promise an atomic permission lease across stores.

For an external effect, a local receipt alone cannot close the crash window
between service execution and receipt storage. Use the original task-step key
with a service that supports idempotency and authoritative receipt lookup. If
the external outcome is unknown, reconcile it; do not blindly rerun. The
``reconcile_required`` state below demonstrates that stop condition without
pretending to implement a generic external executor.
"""

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from threading import RLock

from agent_relationship import RelationshipTracker


class HostContinuationBlocked(RuntimeError):
    """The reference host cannot start this original task step."""


def _identity(task_id, blocked_step_id, revision):
    if any(type(value) is not str or not value.strip() for value in (task_id, blocked_step_id)):
        raise ValueError("task_id and blocked_step_id must be nonempty strings")
    if type(revision) is not int or revision < 1:
        raise ValueError("host revision must be a positive integer")
    encoded = json.dumps([task_id, blocked_step_id, revision], ensure_ascii=False,
                         separators=(",", ":")).encode("utf-8")
    return "host_step_" + hashlib.sha256(encoded).hexdigest()


class LocalTaskHost:
    """A deliberately small example host, not a production workflow engine.

    One row identifies a waiting task step and its host-controlled revision.
    A task cannot be rebound by retrying registration. The receipt identity is
    the original task/step/revision, never a recovery-plan ID: multiple recovered
    incidents must not cause the same original step to run multiple times.
    """

    def __init__(self, path):
        self._lock = RLock()
        self._closed = False
        self._connection = sqlite3.connect(str(path), isolation_level=None,
                                           check_same_thread=False, timeout=5.0)
        try:
            self._connection.execute("PRAGMA synchronous = FULL")
            with self._transaction():
                tables = self._connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
                if tables and {row[0] for row in tables} != {"host_tasks", "host_receipts"}:
                    raise ValueError("the example host requires its own database file")
                self._connection.execute(
                    "CREATE TABLE IF NOT EXISTS host_tasks ("
                    "task_id TEXT PRIMARY KEY, blocked_step_id TEXT NOT NULL, revision INTEGER NOT NULL, "
                    "state TEXT NOT NULL, result_json TEXT)"
                )
                self._connection.execute(
                    "CREATE TABLE IF NOT EXISTS host_receipts ("
                    "effect_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, blocked_step_id TEXT NOT NULL, "
                    "revision INTEGER NOT NULL, receipt_json TEXT NOT NULL, "
                    "UNIQUE(task_id, blocked_step_id, revision))"
                )
        except BaseException:
            self._connection.close()
            self._closed = True
            raise

    def _require_open(self):
        if self._closed:
            raise RuntimeError("example host is closed")

    def _commit(self):
        self._connection.execute("COMMIT")

    @contextmanager
    def _transaction(self):
        with self._lock:
            self._require_open()
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield
                self._commit()
            except BaseException:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise

    def _task(self, task_id):
        row = self._connection.execute(
            "SELECT blocked_step_id, revision, state, result_json FROM host_tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            return None
        return {"task_id": task_id, "blocked_step_id": row[0], "revision": row[1],
                "state": row[2], "result": json.loads(row[3]) if row[3] is not None else None}

    def task(self, task_id):
        """Read the host's task status; SDK recovery status is a separate fact."""
        with self._lock:
            self._require_open()
            return self._task(task_id)

    def register_waiting_task(self, task_id, blocked_step_id, revision=1):
        _identity(task_id, blocked_step_id, revision)
        with self._transaction():
            existing = self._task(task_id)
            if existing is not None:
                if existing["blocked_step_id"] != blocked_step_id or existing["revision"] != revision:
                    raise HostContinuationBlocked("the original task is already bound to a different host step or revision")
                return existing
            self._connection.execute(
                "INSERT INTO host_tasks VALUES (?, ?, ?, 'waiting', NULL)",
                (task_id, blocked_step_id, revision),
            )
            return self._task(task_id)

    def receipt(self, task_id, blocked_step_id, revision=1):
        """Return an already committed receipt independently of current readiness."""
        effect_id = _identity(task_id, blocked_step_id, revision)
        with self._lock:
            self._require_open()
            row = self._connection.execute(
                "SELECT receipt_json FROM host_receipts WHERE effect_id = ?", (effect_id,),
            ).fetchone()
            return json.loads(row[0]) if row else None

    def mark_outcome_unknown(self, task_id, blocked_step_id, revision=1):
        """Stop a waiting step when the host cannot establish an external outcome.

        This is not an external reconciliation implementation. An actual host
        must obtain authoritative evidence before deciding any subsequent work.
        Re-registering the task cannot erase this state.
        """
        _identity(task_id, blocked_step_id, revision)
        with self._transaction():
            task = self._task(task_id)
            if task is None or task["blocked_step_id"] != blocked_step_id or task["revision"] != revision:
                raise HostContinuationBlocked("the requested step is not the current original task step")
            if task["state"] not in ("waiting", "reconcile_required"):
                raise HostContinuationBlocked("the original task step is no longer waiting")
            self._connection.execute("UPDATE host_tasks SET state = 'reconcile_required' WHERE task_id = ?",
                                     (task_id,))
            return self._task(task_id)

    def continue_task(self, tracker, task_id, blocked_step_id, revision=1, *, host_authorized=False):
        """Commit one simulated local result and receipt after host review.

        ``host_authorized`` records the host's policy decision in this example;
        it is not authentication or a permission granted by the SDK. Existing
        receipts remain readable even when authorization or diagnostics change.
        """
        effect_id = _identity(task_id, blocked_step_id, revision)
        with self._transaction():
            previous = self.receipt(task_id, blocked_step_id, revision)
            if previous is not None:
                return previous
            if host_authorized is not True:
                raise HostContinuationBlocked("the host must explicitly authorize continuation")
            task = self._task(task_id)
            if task is None or task["blocked_step_id"] != blocked_step_id or task["revision"] != revision:
                raise HostContinuationBlocked("the requested step is not the current original task step")
            if task["state"] == "reconcile_required":
                raise HostContinuationBlocked("external outcome is unknown; reconcile before further work")
            if task["state"] != "waiting":
                raise HostContinuationBlocked("the original task step is no longer waiting")

            # Recheck ALL task incidents, including other agent pairs. A resolved
            # plan, a positive score or a previously displayed card is not enough.
            view = tracker.task_view(task_id)
            if (view["task_id"] != task_id or view["recovery_status"] != "resolved"
                    or view["open_incident_count"] != 0 or view["active_plan_count"] != 0
                    or view["resolved_incident_count"] < 1):
                raise HostContinuationBlocked("the original task still needs diagnostic review or verified recovery")
            evidence = [{"incident_id": issue["id"], "resolution_event_id": issue["resolution_event_id"]}
                        for issue in view["incidents"] if issue["status"] == "resolved"]
            result = {"kind": "simulated_local_result", "task_id": task_id,
                      "blocked_step_id": blocked_step_id,
                      "message": "模拟：原任务的阻塞步骤已接续，报告草稿已在宿主本地生成。"}
            receipt = {"effect_id": effect_id, "task_id": task_id,
                       "blocked_step_id": blocked_step_id, "revision": revision,
                       "state": "completed", "simulated": True,
                       "recovery_evidence": evidence, "result": result}
            # Both changes are local and commit together. Do not replace this
            # update with an external API call and assume the same guarantee.
            changed = self._connection.execute(
                "UPDATE host_tasks SET state = 'continued', result_json = ? "
                "WHERE task_id = ? AND blocked_step_id = ? AND revision = ? AND state = 'waiting'",
                (json.dumps(result, ensure_ascii=False), task_id, blocked_step_id, revision),
            ).rowcount
            if changed != 1:
                raise HostContinuationBlocked("the host task changed before continuation")
            self._connection.execute("INSERT INTO host_receipts VALUES (?, ?, ?, ?, ?)",
                                     (effect_id, task_id, blocked_step_id, revision,
                                      json.dumps(receipt, ensure_ascii=False)))
            return receipt

    def close(self):
        with self._lock:
            if not self._closed:
                self._connection.close()
                self._closed = True

    def __enter__(self):
        self._require_open()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()


def main():
    task_id, step_id = "host-report-demo-001", "prepare-checked-draft"
    with TemporaryDirectory(prefix="agent-host-demo-") as directory:
        sdk_path = Path(directory) / "relationships.sqlite3"
        host_path = Path(directory) / "host_tasks.sqlite3"
        with RelationshipTracker(storage_path=sdk_path) as tracker, LocalTaskHost(host_path) as host:
            host.register_waiting_task(task_id, step_id)
            failed = tracker.track("organizer", "reviewer", {
                "event_id": "host-demo-failure", "task_id": task_id,
                "action": "handoff", "result": "failed",
                "narrative": "模拟交接失败：报告引用的资料版本不一致。",
                "provenance": {"source": "demo-host", "simulated": True},
            })
            plan = tracker.propose_repair(failed.incident_id)
            tracker.begin_repair(plan["id"], host_authorized=True)
            tracker.track("organizer", "reviewer", {
                "event_id": "host-demo-verification", "task_id": task_id,
                "action": plan["action"], "repair_plan_id": plan["id"],
                "scenario": "success", "result": "success", "verification_passed": True,
                "narrative": "模拟核验：资料版本与交接要求已逐项确认。",
            })
            tracker.complete_repair(plan["id"], "resolved", "host-demo-verification")
            assert host.task(task_id)["state"] == "waiting"

        # Recovery is verified, but the original task is still host-owned and waiting.
        with RelationshipTracker(storage_path=sdk_path) as tracker, LocalTaskHost(host_path) as host:
            assert tracker.task_view(task_id)["recovery_status"] == "resolved"
            receipt = host.continue_task(tracker, task_id, step_id, host_authorized=True)
            assert host.task(task_id)["state"] == "continued"

        # A lost response or process restart reads the SAME receipt, without a second effect.
        with RelationshipTracker(storage_path=sdk_path) as tracker, LocalTaskHost(host_path) as host:
            assert host.continue_task(tracker, task_id, step_id) == receipt
            assert host.receipt(task_id, step_id) == receipt
            host.register_waiting_task("unknown-external-outcome", "send-result")
            host.mark_outcome_unknown("unknown-external-outcome", "send-result")
            try:
                host.continue_task(tracker, "unknown-external-outcome", "send-result", host_authorized=True)
            except HostContinuationBlocked as error:
                assert "reconcile" in str(error)
            else:
                raise AssertionError("an unknown external outcome must not be retried")

        print("离线宿主演示：仅写入临时本地数据库，未执行外部任务。")
        print(f"原任务：{task_id}；原步骤：{step_id}")
        print("异常已核验解决 → 宿主重新确认当前任务与权限 → 接续原步骤并保存回执。")
        print("重开后读取同一回执，未重复生成原步骤结果。")
        print("外部结果不明时：等待宿主核对实际回执，不盲目重试。")


if __name__ == "__main__":
    main()
