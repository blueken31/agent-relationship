"""Synthetic task-evidence walkthrough. No model or external execution.

The authorization, check and receipt below are simulations. A real integration
must bind an immutable artifact and obtain these facts from its own verifier.
"""
import json
import time

from agent_relationship import RelationshipTracker


def run_demo():
    with RelationshipTracker(llm=None, diagnosis_policy='task_evidence_v1') as tracker:
        task = 'demo:handoff'
        issue = tracker.track('author', 'reviewer', {
            'event_id': 'demo:failed', 'task_id': task, 'action': 'check_handoff', 'result': 'failed',
            'narrative': '模拟：交接产出未通过版本一致性检查。',
            'failure_condition': {'artifact_id': 'demo:report', 'check_ids': ['version-consistency']},
        }).incident_id
        tracker.track('author', 'reviewer', {
            'event_id': 'demo:review', 'task_id': task, 'action': 'review', 'result': 'review_reported',
            'narrative': '模拟：评审要求修订；无需估算双方收益。',
        })
        contract = {'artifact_id': 'demo:report', 'artifact_revision': 'demo:revision-2',
                    'check_ids': ['version-consistency'], 'verifier_id': 'demo:host-checker'}
        plan = tracker.propose_repair(issue, 'verify_handoff', verification_contract=contract)
        plan = tracker.begin_repair(plan['id'], host_authorized=True)  # Simulated authorization.
        start = time.time()
        # This is fixture data, not a real check or evidence attestation.
        tracker.track('author', 'reviewer', {
            'event_id': 'demo:verified', 'task_id': task, 'action': plan['action'],
            'repair_plan_id': plan['id'], 'result': 'success', 'verification_passed': True,
            'verification': {
                'artifact_id': contract['artifact_id'], 'artifact_revision': contract['artifact_revision'],
                'verifier_id': contract['verifier_id'], 'receipt_id': 'demo:receipt-1',
                'checks': {'version-consistency': 'passed'}, 'started_at': start, 'finished_at': time.time(),
                'evidence_refs': ['demo:simulated-receipt'],
            },
        })
        before = tracker.task_view(task)
        assert before['primary_action']['label'] == '核对回执并关闭本次问题'
        tracker.complete_repair(plan['id'], 'resolved', 'demo:verified')
        after = tracker.task_view(task)
        assert after['open_incident_count'] == 0 and after['unknown_assessment_count'] == 3
        assert not tracker.can_cooperate('author', 'reviewer', task_id=task)['can']
        return {'simulation': True, 'model_calls': 0, 'before_completion': before['primary_action']['label'],
                'after_completion': after['summary'], 'next_action': after['primary_action']['label'],
                'unknown_assessments_retained': after['unknown_assessment_count'], 'grants_permission': False}


if __name__ == '__main__':
    print(json.dumps(run_demo(), ensure_ascii=False, indent=2))
