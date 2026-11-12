"""Task proof and model uncertainty must not become interchangeable authority."""
from copy import deepcopy
import hashlib
import tempfile
import time
from pathlib import Path
import unittest

from agent_relationship import RelationshipTracker, Assessment
from agent_relationship.llm.base import LLMBackend
from agent_relationship.state import export_state, restore_state, StateError

POLICY = 'task_evidence_v1'
CONTRACT = {'artifact_id': 'candidate', 'artifact_revision': 'v2',
            'check_ids': ['regression'], 'verifier_id': 'host-pytest'}


class FixedAssessment(LLMBackend):
    def __init__(self):
        self.calls = 0
        self.value = Assessment('unknown', 'mock', None, None, 'Cannot quantify effects')

    def chat(self, *args, **kwargs):
        raise AssertionError('No external inference in contract tests')

    def assess_interaction(self, *args, **kwargs):
        self.calls += 1
        return self.value


def event(event_id, **fields):
    return {'event_id': event_id, 'task_id': 'task', 'action': 'review',
            'result': 'review_reported', **fields}


def failure(event_id='failure', **fields):
    return event(event_id, result='failed', failure_condition={
        'artifact_id': 'candidate', 'check_ids': ['regression']}, **fields)


def requirement(event_id='missing', **fields):
    return event(event_id, evidence_requirement={'next_action': 'continue_original_step',
        'artifact_id': 'candidate', 'artifact_revision': 'v2', 'check_ids': ['regression']}, **fields)


class TaskEvidenceTests(unittest.TestCase):
    def tracker(self, **kwargs):
        return RelationshipTracker(llm=None, diagnosis_policy=POLICY, **kwargs)

    def started(self, tracker, original=None):
        observed = tracker.track('implementer', 'reviewer', original or failure())
        plan = tracker.propose_repair(observed.incident_id, 'restricted_retest',
                                     verification_contract=CONTRACT)
        return tracker.begin_repair(plan['id'], host_authorized=True)

    def receipt(self, plan, **overrides):
        value = event('verification', action=plan['action'], result='success',
            repair_plan_id=plan['id'], verification_passed=True,
            verification={'artifact_id': 'candidate', 'artifact_revision': 'v2',
                'verifier_id': 'host-pytest', 'receipt_id': 'run-1',
                'checks': {'regression': 'passed'}, 'evidence_refs': ['run-1.xml'],
                'started_at': plan['started_at'], 'finished_at': time.time()})
        value.update(overrides)
        return value

    def test_unknown_review_is_retained_without_a_repair_item(self):
        backend = FixedAssessment()
        tracker = RelationshipTracker(llm=backend, diagnosis_policy=POLICY)
        row = tracker.track('implementer', 'reviewer', event('review'))
        self.assertEqual(row.assessment_status, 'unknown')
        self.assertIsNone(row.incident_id)
        self.assertIsNone(tracker.event('review')['impact_a'])
        self.assertEqual(tracker.task_queue()['tasks'], [])
        view = tracker.task_view('task')
        self.assertEqual(view['unknown_assessment_count'], 1)
        self.assertEqual(view['recovery_status'], 'observed')
        self.assertFalse(view['grants_permission'])
        self.assertFalse(tracker.can_cooperate('implementer', 'reviewer', task_id='task')['can'])

    def test_disabled_assessment_has_honest_provenance_and_no_trust_gain(self):
        tracker = self.tracker()
        for i in range(8):
            row = tracker.track('implementer', 'reviewer', event(str(i), result='success'))
        health = tracker.health('implementer', 'reviewer', task_id='task')
        self.assertEqual(row.assessment_source, 'not_requested')
        self.assertEqual(health.trust, 0.3)
        self.assertEqual(health.risk, 'unknown')
        with self.assertRaises(ValueError):
            Assessment('assessed', 'not_requested', 1, 1, 'not a model result')

    def test_failure_is_actionable_even_without_an_assessor(self):
        tracker = self.tracker()
        result = tracker.track('implementer', 'reviewer', failure())
        self.assertEqual(tracker.incidents()[0]['category'], 'task_failure')
        self.assertIsNotNone(result.incident_id)
        self.assertEqual(tracker.task_view('task')['recovery_status'], 'needs_attention')

    def test_explicit_missing_check_is_actionable_even_with_positive_scores(self):
        backend = FixedAssessment()
        backend.value = Assessment('assessed', 'mock', 1, 1, 'positive opinion', goal_status='met')
        tracker = RelationshipTracker(llm=backend, diagnosis_policy=POLICY)
        tracker.track('implementer', 'reviewer', requirement())
        issue = tracker.incidents()[0]
        self.assertEqual(issue['category'], 'evidence_needed')
        self.assertEqual(issue['title'], '当前动作缺少必需证据')

    def test_missing_check_scopes_do_not_merge_distinct_revisions_or_actions(self):
        tracker = self.tracker()
        for i, change in enumerate(({}, {'artifact_revision': 'v3'}, {'next_action': 'publish'})):
            value = requirement(str(i))
            value['evidence_requirement'].update(change)
            tracker.track('implementer', 'reviewer', value)
        self.assertEqual(len(tracker.incidents()), 3)

    def test_malformed_conditions_fail_before_assessment_or_mutation(self):
        for condition in (None, {}, {'artifact_id': 'candidate', 'check_ids': []},
                          {'artifact_id': 'candidate', 'check_ids': ['x', 'x']}):
            backend = FixedAssessment()
            tracker = RelationshipTracker(llm=backend, diagnosis_policy=POLICY)
            before = export_state(tracker)
            bad = event('bad', result='failed', failure_condition=condition)
            with self.subTest(condition=condition), self.assertRaises(ValueError):
                tracker.track('implementer', 'reviewer', bad)
            self.assertEqual(backend.calls, 0)
            self.assertEqual(export_state(tracker), before)

    def test_host_receipt_closes_only_bound_failure_and_keeps_unknown_record(self):
        tracker = self.tracker()
        plan = self.started(tracker)
        tracker.track('implementer', 'reviewer', event('legitimate-review'))
        tracker.track('implementer', 'reviewer', self.receipt(plan))
        before = export_state(tracker)
        card = tracker.task_view('task')['incidents'][0]
        self.assertTrue(card['host_verification']['contract_match'])
        self.assertEqual(card['next_action']['label'], '核对回执并关闭本次问题')
        self.assertFalse(card['next_action']['grants_permission'])
        self.assertEqual(export_state(tracker), before)
        tracker.complete_repair(plan['id'], 'resolved', 'verification')
        self.assertEqual(tracker.incidents(status='open'), [])
        self.assertEqual(tracker.event('verification')['assessment_status'], 'unknown')
        self.assertEqual(tracker.task_view('task')['summary'], '本次问题已按登记条件核验关闭')
        self.assertFalse(tracker.can_cooperate('implementer', 'reviewer', task_id='task')['can'])

    def test_missing_requirement_can_close_with_its_matching_check(self):
        tracker = self.tracker()
        plan = self.started(tracker, requirement())
        tracker.track('implementer', 'reviewer', self.receipt(plan))
        tracker.complete_repair(plan['id'], 'resolved', 'verification')
        self.assertEqual(tracker.task_view('task')['open_incident_count'], 0)

    def test_wrong_receipts_do_not_resolve_the_incident(self):
        changes = [
            {'artifact_id': 'other'}, {'artifact_revision': 'v1'}, {'verifier_id': 'implementer'},
            {'receipt_id': ''}, {'checks': {}}, {'checks': {'unrelated': 'passed'}},
            {'checks': {'regression': True}}, {'checks': {'regression': 'failed'}},
            {'checks': {'regression': 'skipped'}}, {'evidence_refs': []},
            {'started_at': True}, {'started_at': 0}, {'finished_at': time.time() + 1000},
        ]
        for change in changes:
            tracker = self.tracker(); plan = self.started(tracker)
            value = self.receipt(plan); value['verification'].update(change)
            tracker.track('implementer', 'reviewer', value)
            with self.subTest(change=change), self.assertRaises(ValueError):
                tracker.complete_repair(plan['id'], 'resolved', 'verification')
            self.assertEqual(len(tracker.incidents(status='open')), 1)

    def test_host_boolean_and_task_pair_action_plan_bindings_remain_required(self):
        changes = [{'verification_passed': 'true'}, {'verification_passed': 1},
                   {'result': 'partial'}, {'task_id': 'other'}, {'action': 'other'},
                   {'repair_plan_id': 'other'}, {'verification': None}]
        for change in changes:
            tracker = self.tracker(); plan = self.started(tracker)
            tracker.track('implementer', 'reviewer', self.receipt(plan, **change))
            with self.subTest(change=change), self.assertRaises(ValueError):
                tracker.complete_repair(plan['id'], 'resolved', 'verification')
        tracker = self.tracker(); plan = self.started(tracker)
        tracker.track('foreign', 'reviewer', self.receipt(plan))
        with self.assertRaises(ValueError):
            tracker.complete_repair(plan['id'], 'resolved', 'verification')

    def test_plan_contract_cannot_change_or_omit_original_checks(self):
        tracker = self.tracker()
        issue = tracker.track('implementer', 'reviewer', failure()).incident_id
        for change in ({'artifact_id': 'other'}, {'check_ids': ['other']}, {'check_ids': []}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                tracker.propose_repair(issue, verification_contract={**CONTRACT, **change})
        plan = tracker.propose_repair(issue, verification_contract=CONTRACT)
        self.assertEqual(tracker.propose_repair(issue, verification_contract=deepcopy(CONTRACT)), plan)
        with self.assertRaises(ValueError):
            tracker.propose_repair(issue, verification_contract={**CONTRACT, 'artifact_revision': 'v3'})
        with self.assertRaises(ValueError):
            tracker.begin_repair(plan['id'], host_authorized='yes')

    def test_contract_requires_original_structured_condition(self):
        tracker = self.tracker()
        issue = tracker.track('implementer', 'reviewer', event('failed', result='failed')).incident_id
        with self.assertRaisesRegex(ValueError, 'structured evidence condition'):
            tracker.propose_repair(issue, verification_contract=CONTRACT)

    def test_requirement_revision_cannot_be_substituted(self):
        tracker = self.tracker()
        issue = tracker.track('implementer', 'reviewer', requirement()).incident_id
        with self.assertRaisesRegex(ValueError, 'revision'):
            tracker.propose_repair(issue, verification_contract={**CONTRACT, 'artifact_revision': 'v3'})

    def test_new_required_checks_invalidate_a_proposed_contract_before_begin(self):
        tracker = self.tracker()
        issue = tracker.track('implementer', 'reviewer', failure()).incident_id
        plan = tracker.propose_repair(issue, verification_contract=CONTRACT)
        extra = failure('new-check'); extra['failure_condition']['check_ids'] = ['other']
        tracker.track('implementer', 'reviewer', extra)
        with self.assertRaises(ValueError):
            tracker.begin_repair(plan['id'], host_authorized=True)
        self.assertEqual(tracker.repair_plan(plan['id'])['state'], 'proposed')

    def test_new_failure_cancellation_and_semantic_harm_cannot_be_washed_away(self):
        for mode in ('new_failure', 'cancelled', 'harm', 'goal_without_scores'):
            backend = FixedAssessment()
            tracker = RelationshipTracker(llm=backend, diagnosis_policy=POLICY)
            plan = self.started(tracker)
            if mode == 'new_failure':
                tracker.track('implementer', 'reviewer', failure('new-failure'))
            elif mode == 'cancelled':
                tracker.cancel_repair(plan['id'])
            else:
                backend.value = (Assessment('assessed', 'mock', -.8, -.8, 'observed harm')
                                 if mode == 'harm' else
                                 Assessment('unknown', 'mock', None, None, 'known goal violation', goal_status='violated'))
                tracker.track('implementer', 'reviewer', event('hazard'))
                backend.value = Assessment('unknown', 'mock', None, None, 'effects unknown')
            tracker.track('implementer', 'reviewer', self.receipt(plan))
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                tracker.complete_repair(plan['id'], 'resolved', 'verification')

    def test_semantic_harm_has_no_objective_test_escape(self):
        backend = FixedAssessment(); backend.value = Assessment('assessed', 'mock', -.8, -.8, 'harm')
        tracker = RelationshipTracker(llm=backend, diagnosis_policy=POLICY)
        issue = tracker.track('implementer', 'reviewer', failure()).incident_id
        with self.assertRaisesRegex(ValueError, 'semantic harm'):
            tracker.propose_repair(issue, verification_contract=CONTRACT)

    def test_restart_preserves_policy_contract_and_read_only_results(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.sqlite3'
            with self.tracker(storage_path=path) as tracker:
                plan = self.started(tracker)
            with self.tracker(storage_path=path) as tracker:
                tracker.track('implementer', 'reviewer', self.receipt(plan))
                tracker.complete_repair(plan['id'], 'resolved', 'verification')
                view = tracker.task_view('task'); saved = export_state(tracker)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            with self.tracker(storage_path=path) as tracker:
                self.assertEqual(tracker.task_view('task'), view)
                self.assertEqual(export_state(tracker), saved)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
            self.assertEqual(saved['schema_version'], 2)
            with self.assertRaises(StateError):
                RelationshipTracker(storage_path=path)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)

    def test_legacy_checkpoints_and_unknown_behavior_remain_unchanged(self):
        legacy = RelationshipTracker(llm=None)
        legacy.track('implementer', 'reviewer', event('unknown'))
        saved = export_state(legacy)
        self.assertEqual(saved['schema_version'], 1)
        self.assertEqual(len(legacy.incidents()), 1)
        with self.assertRaises(StateError):
            restore_state(self.tracker(), saved)

    def test_restore_rejects_changed_closed_contract_without_partial_mutation(self):
        tracker = self.tracker(); plan = self.started(tracker)
        tracker.track('implementer', 'reviewer', self.receipt(plan))
        tracker.complete_repair(plan['id'], 'resolved', 'verification')
        saved = export_state(tracker)
        saved['recovery']['plans'][plan['id']]['verification_contract']['artifact_revision'] = 'wrong'
        target = self.tracker(); before = export_state(target)
        with self.assertRaises(StateError):
            restore_state(target, saved)
        self.assertEqual(export_state(target), before)

    def test_stale_contracts_remain_readable_and_cancellable_after_restart(self):
        for state in ('proposed', 'started'):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'state.sqlite3'
                with self.tracker(storage_path=path) as tracker:
                    issue = tracker.track('implementer', 'reviewer', failure()).incident_id
                    plan = tracker.propose_repair(issue, verification_contract=CONTRACT)
                    if state == 'started':
                        tracker.begin_repair(plan['id'], host_authorized=True)
                    extra = failure('new-check')
                    extra['failure_condition']['check_ids'] = ['other']
                    tracker.track('implementer', 'reviewer', extra)
                with self.tracker(storage_path=path) as tracker:
                    self.assertEqual(tracker.repair_plan(plan['id'])['state'], state)
                    self.assertEqual(tracker.task_view('task')['primary_action']['kind'], 'reconcile_plan')
                    if state == 'proposed':
                        with self.assertRaises(ValueError):
                            tracker.begin_repair(plan['id'], host_authorized=True)
                    tracker.cancel_repair(plan['id'])
                with self.tracker(storage_path=path) as tracker:
                    self.assertEqual(tracker.repair_plan(plan['id'])['state'], 'cancelled')

    def test_new_hazard_after_receipt_still_prevents_closure(self):
        backend = FixedAssessment()
        tracker = RelationshipTracker(llm=backend, diagnosis_policy=POLICY)
        plan = self.started(tracker)
        tracker.track('implementer', 'reviewer', self.receipt(plan))
        backend.value = Assessment('assessed', 'mock', -.8, -.8, 'new harm after verification')
        tracker.track('implementer', 'reviewer', event('hazard'))
        with self.assertRaisesRegex(ValueError, 'new adverse'):
            tracker.complete_repair(plan['id'], 'resolved', 'verification')
        card = next(c for c in tracker.task_view('task')['incidents'] if c['id'] == plan['incident_id'])
        self.assertEqual(card['next_action']['kind'], 'reconcile_plan')

    def test_hazard_after_closure_keeps_original_resolution_and_opens_separate_issue(self):
        backend = FixedAssessment()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.sqlite3'
            with RelationshipTracker(llm=backend, diagnosis_policy=POLICY, storage_path=path) as tracker:
                plan = self.started(tracker)
                tracker.track('implementer', 'reviewer', self.receipt(plan))
                tracker.complete_repair(plan['id'], 'resolved', 'verification')
                backend.value = Assessment('assessed', 'mock', -.8, -.8, 'subsequent harm')
                tracker.track('implementer', 'reviewer', event('later-hazard'))
            with self.tracker(storage_path=path) as tracker:
                self.assertEqual(tracker.repair_plan(plan['id'])['outcome'], 'resolved')
                self.assertEqual(len(tracker.incidents(status='open')), 1)
                self.assertEqual(tracker.task_view('task')['recovery_status'], 'needs_attention')

    def test_other_task_or_pair_does_not_invalidate_this_bounded_check(self):
        for task_id, actor in (('other-task', 'implementer'), ('task', 'other-actor')):
            backend = FixedAssessment()
            tracker = RelationshipTracker(llm=backend, diagnosis_policy=POLICY)
            plan = self.started(tracker)
            backend.value = Assessment('assessed', 'mock', -.8, -.8, 'unrelated harm')
            tracker.track(actor, 'reviewer', event('hazard', task_id=task_id))
            backend.value = Assessment('unknown', 'mock', None, None, 'unknown effects')
            tracker.track('implementer', 'reviewer', self.receipt(plan))
            tracker.complete_repair(plan['id'], 'resolved', 'verification')
            self.assertEqual(len(tracker.incidents(status='open')), 1)

    def test_bad_contract_receipt_is_visible_without_promising_closure(self):
        tracker = self.tracker(); plan = self.started(tracker)
        value = self.receipt(plan); value['verification']['artifact_revision'] = 'old'
        tracker.track('implementer', 'reviewer', value)
        card = tracker.task_view('task')['incidents'][0]
        self.assertEqual(card['host_verification']['status'], 'passed')
        self.assertFalse(card['host_verification']['contract_match'])
        self.assertEqual(card['next_action']['label'], '核对未满足的验证条件')

    def test_restore_rejects_completion_sequence_before_verification(self):
        tracker = self.tracker(); plan = self.started(tracker)
        tracker.track('implementer', 'reviewer', self.receipt(plan))
        tracker.complete_repair(plan['id'], 'resolved', 'verification')
        saved = export_state(tracker)
        saved['recovery']['plans'][plan['id']]['completion_sequence'] = 1
        with self.assertRaises(StateError):
            restore_state(self.tracker(), saved)

    def test_new_next_action_requirement_does_not_inherit_another_issues_plan(self):
        tracker = self.tracker(); plan = self.started(tracker)
        value = self.receipt(plan)
        value['evidence_requirement'] = {
            'next_action': 'publish', 'artifact_id': 'candidate', 'artifact_revision': 'v2',
            'check_ids': ['publication-approval'],
        }
        row = tracker.track('implementer', 'reviewer', value)
        card = next(c for c in tracker.task_view('task')['incidents'] if c['id'] == row.incident_id)
        self.assertIsNone(card['related_incident_id'])
        self.assertEqual(card['next_action']['kind'], 'prepare_repair')
        tracker.complete_repair(plan['id'], 'resolved', 'verification')
        self.assertEqual(tracker.task_view('task')['open_incident_count'], 1)
        self.assertEqual(tracker.task_view('task')['primary_action']['incident_id'], row.incident_id)


if __name__ == '__main__':
    unittest.main()
