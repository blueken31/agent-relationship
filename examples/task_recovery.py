"""Offline task recovery walkthrough; all task execution and policy are simulated.

Run from the repository root:
    PYTHONPATH=. python examples/task_recovery.py

No model calls, credentials, network requests or external task execution occur.
"""

from agent_relationship import RelationshipTracker


def simulate_host_authorization(plan):
    """Demo policy only. A real host must check its own user/action permissions."""
    return (
        plan["task_id"] == "report-demo-001"
        and plan["action"] == "verify_handoff"
        and plan["max_attempts"] == 1
        and plan["grants_permission"] is False
    )


def simulate_host_verification(task, plan):
    """Demo evidence only. Replace with the host's actual bounded check."""
    assert task["id"] == plan["task_id"]
    # This is a fixture, not a real document comparison or an LLM verdict.
    return {
        "event_id": "verify-demo-001",
        "task_id": task["id"],
        "action": plan["action"],
        "repair_plan_id": plan["id"],
        "scenario": "success",
        "goal": "Produce the report from one verified input version",
        "expected_result": "Both agents use the same checked source version",
        "result": "success",
        "verification_passed": True,
        "narrative": "模拟核验完成：双方资料版本和验收要求一致。",
        "evidence_refs": ["fixture:version-check-001"],
        "provenance": {"source": "demo-host", "simulated": True},
    }


def main():
    tracker = RelationshipTracker(llm="mock")
    task = {"id": "report-demo-001", "state": "awaiting_verification"}
    original_task_id = task["id"]
    event = {
        "event_id": "handoff-demo-001",
        "task_id": task["id"],
        "action": "handoff",
        "goal": "Produce the report from one verified input version",
        "expected_result": "Both agents use the same checked source version",
        "result": "failed",
        "narrative": "模拟交接失败：整理与审核使用了不同版本的资料。",
        "evidence_refs": ["fixture:handoff-001"],
        "provenance": {"source": "demo-host", "simulated": True},
    }
    failed = tracker.track("organizer", "reviewer", event)
    assert failed.assessment_source == "mock"
    assert failed.incident_id is not None

    # A retry of the identical event does not evaluate or count it twice.
    retried = tracker.track("organizer", "reviewer", event)
    assert retried.duplicate is True
    assert retried.interaction_count == failed.interaction_count

    issues = tracker.incidents(task_id=task["id"], status="open")
    assert len(issues) == 1
    issue = issues[0]
    assert issue["id"] == failed.incident_id
    assert issue["task_id"] == original_task_id
    evidence = tracker.history("organizer", "reviewer", task_id=task["id"])
    assert evidence[0]["narrative"] == event["narrative"]

    plan = tracker.propose_repair(issue["id"], action="verify_handoff")
    assert plan["grants_permission"] is False
    assert plan["state"] == "proposed"
    started = tracker.begin_repair(
        plan["id"], host_authorized=simulate_host_authorization(plan),
    )
    assert started["state"] == "started"

    verification = tracker.track(
        "organizer", "reviewer", simulate_host_verification(task, started),
    )
    completed = tracker.complete_repair(
        plan["id"], outcome="resolved", evidence_event_id=verification.event_id,
    )
    assert completed["state"] == "completed"
    assert completed["outcome"] == "resolved"
    assert completed["task_id"] == original_task_id
    assert tracker.incidents(task_id=task["id"], status="open") == []
    assert tracker.incidents(task_id=task["id"], status="resolved")[0]["resolution_event_id"] == verification.event_id

    # In this demonstration only, the host marks the same task ready to resume.
    # Closing an issue does not reset historical scores or grant task authority.
    task["state"] = "ready_to_resume"
    assert task["id"] == original_task_id
    print("离线模拟：未调用真实模型，也未执行外部任务。")
    print(f"任务：{task['id']}")
    print(f"发现：{issue['reason']}")
    print(f"建议：{plan['description']}；最多执行 {plan['max_attempts']} 次。")
    print(f"范围：{plan['scope']}")
    print("结果：模拟核验已通过，对应异常已解决。")
    print("原任务：保留原任务标识，模拟宿主已标记为可继续。")


if __name__ == "__main__":
    main()
