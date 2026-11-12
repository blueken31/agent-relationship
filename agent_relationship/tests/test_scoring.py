"""Regression properties for harm, missing evidence and directional impacts."""

import unittest
from agent_relationship.models import InteractionRecord, RelationProfile, RelationType


def record(a=0.0, b=0.0, **kwargs):
    return InteractionRecord(
        timestamp=1.0, interaction_type="fixture", initiator="a", target="b",
        impact_a=a, impact_b=b, **kwargs,
    )


class ScoringSafetyTests(unittest.TestCase):
    def profile(self, a, b, **kwargs):
        p = RelationProfile("a", "b")
        p.record_interaction(record(a, b, **kwargs))
        return p

    def test_equal_gain_and_loss_does_not_cancel(self):
        p = self.profile(0.9, -0.9)
        self.assertLess(p.balance, 0.1)
        self.assertEqual(p.relation_type, RelationType.PARASITIC)

    def test_increasing_harm_cannot_improve_balance(self):
        balances = [self.profile(0.9, -loss).balance for loss in (0.1, 0.3, 0.6, 0.9)]
        self.assertEqual(balances, sorted(balances, reverse=True))
        self.assertGreater(balances[0], balances[-1])

    def test_increasing_benefit_cannot_cancel_existing_harm(self):
        low_gain = self.profile(0.1, -0.5)
        high_gain = self.profile(0.9, -0.5)
        self.assertEqual(low_gain.balance, high_gain.balance)

    def test_harm_without_beneficiary_is_negative(self):
        p = self.profile(0.0, -1.0)
        self.assertLess(p.balance, 0.1)
        self.assertLess(p.trust, 0.3)
        self.assertEqual(p.relation_type, RelationType.CONFLICTUAL)
        self.assertEqual(p.conflictual_count, 1)

    def test_small_harm_never_raises_low_trust(self):
        p = RelationProfile("a", "b", trust=0.01)
        p.record_interaction(record(0, -0.001))
        self.assertLessEqual(p.trust, 0.01)

    def test_both_gaining_cannot_hide_goal_violation(self):
        p = self.profile(0.9, 0.9, goal_status="violated")
        self.assertLess(p.balance, 0.1)
        self.assertLess(p.trust, 0.3)
        self.assertEqual(p.symbiotic_count, 0)
        self.assertEqual(p.relation_type, RelationType.CONFLICTUAL)

    def test_relation_type_does_not_follow_score_bands(self):
        p = self.profile(0.001, 0.001)
        self.assertLess(p.balance, 0.7)
        self.assertEqual(p.relation_type, RelationType.SYMBIOTIC)
        single_beneficiary = self.profile(1.0, 0.0)
        self.assertGreater(single_beneficiary.balance, 0.7)
        self.assertEqual(single_beneficiary.relation_type, RelationType.COMMENSAL)

    def test_symmetric_benefits_have_zero_asymmetry(self):
        p = RelationProfile("a", "b")
        for _ in range(5):
            p.record_interaction(record(0.5, 0.5))
        self.assertEqual(p.power_asymmetry, 0.0)

    def test_asymmetry_maps_canonical_agents_not_initiator_positions(self):
        p = RelationProfile("a", "b")
        p.record_interaction(record(0.5, 0.0))
        # Same beneficiary a, though event direction changes.
        p.record_interaction(InteractionRecord(
            2, "fixture", "b", "a", impact_a=0, impact_b=0.5,
        ))
        self.assertEqual(p.power_asymmetry, 1.0)

    def test_swapping_both_participants_preserves_balance_and_asymmetry(self):
        forward = self.profile(0.7, -0.2)
        reverse = RelationProfile("a", "b")
        reverse.record_interaction(InteractionRecord(
            1, "fixture", "b", "a", impact_a=-0.2, impact_b=0.7,
        ))
        self.assertEqual(forward.balance, reverse.balance)
        self.assertEqual(forward.power_asymmetry, reverse.power_asymmetry)

    def test_unknown_does_not_change_scores_after_history_eviction(self):
        p = RelationProfile("a", "b", max_history_length=1)
        p.record_interaction(record(-1, -1))
        old = (p.balance, p.trust, p.relation_type, p.confidence)
        for _ in range(3):
            p.record_interaction(record(None, None, assessment_status="unknown"))
        p._recalculate_balance()
        p._update_relation_type()
        self.assertEqual(old, (p.balance, p.trust, p.relation_type, p.confidence))
        self.assertEqual(p.interaction_count, 4)
        self.assertEqual(p.assessed_count, 1)
        self.assertIsNone(p.interaction_history[0].net_impact)

    def test_unknown_only_keeps_cold_start_and_no_confidence(self):
        p = self.profile(None, None, assessment_status="unknown")
        self.assertEqual((p.balance, p.trust, p.confidence), (0.5, 0.3, 0.0))
        self.assertEqual(p.relation_type, RelationType.NEUTRAL)
        self.assertEqual(p.symbiotic_count, 0)

    def test_unknown_numeric_values_never_become_assessed_evidence(self):
        p = self.profile(1, 1, assessment_status="unknown")
        self.assertEqual((p.balance, p.trust, p.assessed_count), (0.5, 0.3, 0))

    def test_confidence_depends_on_assessed_count_not_score(self):
        good = self.profile(1, 1)
        bad = self.profile(-1, -1)
        self.assertEqual(good.confidence, bad.confidence)
        self.assertLess(good.confidence, 0.2)

    def test_unknown_does_not_reweight_prior_events(self):
        p = RelationProfile("a", "b")
        p.record_interaction(record(0.5, 0.5))
        p.record_interaction(record(-0.5, -0.5))
        balance = p.balance
        for _ in range(4):
            p.record_interaction(record(None, None, assessment_status="unknown"))
        p._recalculate_balance()
        self.assertEqual(p.balance, balance)

    def test_nonpositive_and_noninteger_history_limits_are_rejected(self):
        for value in (0, -1, 0.5, True, "5"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                RelationProfile("a", "b", max_history_length=value)

    def test_invalid_impacts_are_rejected_even_in_unknown_records(self):
        for value in (True, False, "0.5", float("nan"), float("inf"), -1.1, 1.1, 10 ** 1000):
            for status in ("assessed", "unknown"):
                with self.subTest(value=value, status=status), self.assertRaises(ValueError):
                    record(value, 0.0, assessment_status=status)

    def test_missing_assessed_impact_is_rejected(self):
        with self.assertRaises(ValueError):
            record(None, 0.0)

    def test_invalid_assessment_and_goal_status_are_rejected(self):
        with self.assertRaises(ValueError):
            record(0, 0, assessment_status="timeout")
        with self.assertRaises(ValueError):
            record(0, 0, goal_status="maybe")

    def test_wrong_participants_rejected_without_mutation(self):
        p = RelationProfile("a", "b")
        with self.assertRaises(ValueError):
            p.record_interaction(InteractionRecord(1, "fixture", "a", "c"))
        self.assertEqual(p.interaction_count, 0)
        self.assertEqual(p.interaction_history, [])


if __name__ == "__main__":
    unittest.main()
