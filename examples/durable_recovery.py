"""Offline SQLite recovery walkthrough; host actions are explicit fixtures.

Run from the repository root:
    PYTHONPATH=. python examples/durable_recovery.py

The temporary database is deleted when the example ends. No credentials, model
calls, network requests or external task execution are involved.
"""

from pathlib import Path
from tempfile import TemporaryDirectory

from agent_relationship import RelationshipTracker


def main():
    task_id = "durable-report-demo-001"
    failed_event = {
        "event_id": "durable-handoff-demo-001",
        "task_id": task_id,
        "action": "handoff",
        "goal": "Produce a report using verified source data",
        "expected_result": "Both agents use the same checked source version",
        "result": "failed",
        "narrative": "模拟交接失败：双方资料版本不一致。",
        "provenance": {"source": "demo-host", "simulated": True},
    }
    with TemporaryDirectory(prefix="agent-relationship-demo-") as temporary:
        database = str(Path(temporary) / "relationships.sqlite3")
        with RelationshipTracker(storage_path=database) as tracker:
            failed = tracker.track("organizer", "reviewer", failed_event)
            incident_id = failed.incident_id
            assert incident_id is not None
            plan = tracker.propose_repair(incident_id)
            assert plan["task_id"] == task_id
            assert plan["max_attempts"] == 1
            assert plan["grants_permission"] is False

            # A real host checks its policy first. This authorization is a fixture.
            tracker.begin_repair(plan["id"], host_authorized=True)

        # Reopening restores bookkeeping, not external execution.
        with RelationshipTracker(storage_path=database) as resumed:
            pending = resumed.incidents(task_id=task_id, status="open")
            assert len(pending) == 1
            assert pending[0]["id"] == incident_id
            restored_plan = resumed.propose_repair(incident_id)
            assert restored_plan["id"] == plan["id"]
            assert restored_plan["state"] == "started"
            assert not resumed.can_cooperate("organizer", "reviewer", task_id=task_id)["can"]

            # The committed event is returned without assessment or accounting again.
            duplicate = resumed.track("organizer", "reviewer", failed_event)
            assert duplicate.duplicate is True
            assert duplicate.incident_id == incident_id
            assert duplicate.interaction_count == failed.interaction_count
            assert resumed.event(failed.event_id)["narrative"] == failed_event["narrative"]

            # This new fixture stands in for the host's actual bounded verification.
            verified = resumed.track("organizer", "reviewer", {
                "event_id": "durable-verification-demo-001",
                "task_id": task_id,
                "action": restored_plan["action"],
                "repair_plan_id": restored_plan["id"],
                "scenario": "success",
                "result": "success",
                "verification_passed": True,
                "narrative": "模拟核验完成：资料版本与验收条件一致。",
                "provenance": {"source": "demo-host", "simulated": True},
            })
            completed = resumed.complete_repair(plan["id"], "resolved", verified.event_id)
            assert completed["task_id"] == task_id
            assert completed["state"] == "completed"

        with RelationshipTracker(storage_path=database) as checked:
            assert checked.incidents(task_id=task_id, status="open") == []
            resolved = checked.incidents(task_id=task_id, status="resolved")
            assert len(resolved) == 1
            assert resolved[0]["id"] == incident_id
            assert resolved[0]["resolution_event_id"] == verified.event_id
            assert checked.complete_repair(plan["id"], "resolved", verified.event_id) == completed
            assert checked.event(failed.event_id)["task_id"] == task_id

        print("离线模拟：未调用真实模型，也未执行外部任务。")
        print(f"原任务：{task_id}")
        print("第一次重开：原异常、已开始的恢复方案及事件依据均已保留。")
        print("重复事件：返回原结果，未重复记账。")
        print("第二次重开：对应核验结果和异常已解决状态均已保留。")
        print("宿主任务：仍由宿主决定何时接续；SDK 不自动重新执行。")


if __name__ == "__main__":
    main()
