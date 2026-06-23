"""
Agent Relationship — 单元测试套件
"""

import unittest
import time
from agent_relationship.models import (
    RelationType,
    InteractionRecord,
    RelationProfile,
)
from agent_relationship.repair import RepairMechanism
from agent_relationship.engine import RelationshipEngine
from agent_relationship.tracker import RelationshipTracker
from agent_relationship.llm import MockLLM, LLMBackend


# ═══════════════════════════════════════════════════════════════
# RelationType
# ═══════════════════════════════════════════════════════════════


class TestRelationType(unittest.TestCase):
    def test_all_values_present(self):
        types = [
            RelationType.SYMBIOTIC,
            RelationType.COMMENSAL,
            RelationType.PARASITIC,
            RelationType.COMPETITIVE,
            RelationType.CONFLICTUAL,
            RelationType.NEUTRAL,
        ]
        self.assertEqual(len(types), 6)

    def test_value_access(self):
        self.assertEqual(RelationType.SYMBIOTIC.value, "symbiotic")


# ═══════════════════════════════════════════════════════════════
# InteractionRecord
# ═══════════════════════════════════════════════════════════════


class TestInteractionRecord(unittest.TestCase):
    def test_symbiotic_both_positive(self):
        r = InteractionRecord(
            timestamp=time.time(),
            interaction_type="collaboration",
            initiator="a",
            target="b",
            impact_a=0.5,
            impact_b=0.3,
        )
        self.assertTrue(r.is_symbiotic)
        self.assertFalse(r.is_parasitic)
        self.assertFalse(r.is_conflictual)
        self.assertEqual(r.net_impact, 0.8)

    def test_parasitic_one_negative(self):
        r = InteractionRecord(
            timestamp=time.time(),
            interaction_type="exploit",
            initiator="a",
            target="b",
            impact_a=0.5,
            impact_b=-0.2,
        )
        self.assertFalse(r.is_symbiotic)
        self.assertTrue(r.is_parasitic)
        self.assertFalse(r.is_conflictual)

    def test_conflictual_both_negative(self):
        r = InteractionRecord(
            timestamp=time.time(),
            interaction_type="conflict",
            initiator="a",
            target="b",
            impact_a=-0.3,
            impact_b=-0.4,
        )
        self.assertFalse(r.is_symbiotic)
        self.assertFalse(r.is_parasitic)
        self.assertTrue(r.is_conflictual)

    def test_swapped_parasitic(self):
        r = InteractionRecord(
            timestamp=time.time(),
            interaction_type="exploit",
            initiator="a",
            target="b",
            impact_a=-0.2,
            impact_b=0.5,
        )
        self.assertTrue(r.is_parasitic)


# ═══════════════════════════════════════════════════════════════
# RelationProfile
# ═══════════════════════════════════════════════════════════════


class TestRelationProfile(unittest.TestCase):
    def test_default_values(self):
        p = RelationProfile(agent_a="x", agent_b="y")
        self.assertEqual(p.balance, 0.5)
        self.assertEqual(p.trust, 0.3)
        self.assertEqual(p.relation_type, RelationType.NEUTRAL)
        self.assertEqual(p.interaction_count, 0)

    def test_empty_history_stays_neutral(self):
        p = RelationProfile(agent_a="x", agent_b="y")
        p._recalculate_balance()
        self.assertAlmostEqual(p.balance, 0.5, places=2)

    def test_symbiotic_raises_balance(self):
        p = RelationProfile(agent_a="x", agent_b="y")
        for _ in range(5):
            r = InteractionRecord(
                timestamp=time.time(),
                interaction_type="help",
                initiator="x",
                target="y",
                impact_a=0.6,
                impact_b=0.4,
            )
            p.record_interaction(r)
        self.assertGreater(p.balance, 0.7)
        self.assertEqual(p.relation_type, RelationType.SYMBIOTIC)

    def test_conflictual_lowers_balance(self):
        p = RelationProfile(agent_a="x", agent_b="y")
        for _ in range(5):
            r = InteractionRecord(
                timestamp=time.time(),
                interaction_type="conflict",
                initiator="x",
                target="y",
                impact_a=-0.5,
                impact_b=-0.5,
            )
            p.record_interaction(r)
        self.assertLess(p.balance, 0.3)
        self.assertIn(
            p.relation_type,
            [RelationType.COMPETITIVE, RelationType.PARASITIC, RelationType.CONFLICTUAL],
        )

    def test_balance_bounded_zero_one(self):
        p = RelationProfile(agent_a="x", agent_b="y")
        # 大量冲突
        for _ in range(20):
            r = InteractionRecord(
                timestamp=time.time(),
                interaction_type="conflict",
                initiator="x",
                target="y",
                impact_a=-0.9,
                impact_b=-0.9,
            )
            p.record_interaction(r)
        self.assertGreaterEqual(p.balance, 0.0)
        self.assertLessEqual(p.balance, 1.0)

    def test_recent_weighted_higher(self):
        """最新交互权重更高"""
        p = RelationProfile(agent_a="x", agent_b="y")
        # 先做 4 次良性
        for _ in range(4):
            r = InteractionRecord(
                timestamp=time.time(),
                interaction_type="help",
                initiator="x",
                target="y",
                impact_a=0.5,
                impact_b=0.5,
            )
            p.record_interaction(r)
        high_balance = p.balance

        # 一次恶性
        r = InteractionRecord(
            timestamp=time.time(),
            interaction_type="conflict",
            initiator="x",
            target="y",
            impact_a=-0.8,
            impact_b=-0.8,
        )
        p.record_interaction(r)
        # 最近交互权重高，应该明显下拉
        self.assertLess(p.balance, high_balance)

    def test_counts_updated(self):
        p = RelationProfile(agent_a="x", agent_b="y")
        p.record_interaction(
            InteractionRecord(time.time(), "help", "x", "y", impact_a=0.5, impact_b=0.5)
        )
        p.record_interaction(
            InteractionRecord(time.time(), "help", "x", "y", impact_a=0.6, impact_b=-0.1)
        )
        p.record_interaction(
            InteractionRecord(
                time.time(), "fight", "x", "y", impact_a=-0.5, impact_b=-0.5
            )
        )
        self.assertEqual(p.interaction_count, 3)
        self.assertEqual(p.symbiotic_count, 1)
        self.assertEqual(p.parasitic_count, 1)
        self.assertEqual(p.conflictual_count, 1)

    def test_history_capped(self):
        p = RelationProfile(agent_a="x", agent_b="y", max_history_length=5)
        for i in range(10):
            r = InteractionRecord(
                timestamp=time.time(),
                interaction_type=f"action_{i}",
                initiator="x",
                target="y",
                impact_a=0.1,
                impact_b=0.1,
            )
            p.record_interaction(r)
        self.assertEqual(len(p.interaction_history), 5)


# ═══════════════════════════════════════════════════════════════
# RepairMechanism
# ═══════════════════════════════════════════════════════════════


class TestRepairMechanism(unittest.TestCase):
    def setUp(self):
        self.repair = RepairMechanism()

    def test_can_repair_above_threshold(self):
        p = RelationProfile(agent_a="x", agent_b="y")
        p.balance = 0.5
        can, msg = self.repair.can_attempt_repair(p)
        self.assertTrue(can)

    def test_can_repair_below_threshold_no_cooldown(self):
        p = RelationProfile(agent_a="x", agent_b="y")
        p.balance = 0.2
        can, msg = self.repair.can_attempt_repair(p)
        self.assertTrue(can)

    def test_cooldown_active(self):
        p = RelationProfile(agent_a="x", agent_b="y")
        p.balance = 0.2
        p.last_repair_time = time.time()  # 刚刚修过
        can, msg = self.repair.can_attempt_repair(p)
        self.assertFalse(can)
        self.assertIn("冷却", msg)

    def test_execute_repair_updates_count(self):
        p = RelationProfile(agent_a="x", agent_b="y")
        p.balance = 0.15
        result = self.repair.execute_repair_attempt(p)
        self.assertEqual(result["status"], "repair_granted")
        self.assertEqual(p.repair_attempts, 1)
        self.assertLessEqual(result["temp_threshold"], 0.3)

    def test_available_paths_count(self):
        paths = self.repair.available_paths()
        self.assertEqual(len(paths), 3)


# ═══════════════════════════════════════════════════════════════
# RelationshipEngine
# ═══════════════════════════════════════════════════════════════


class TestRelationshipEngine(unittest.TestCase):
    def setUp(self):
        self.llm = MockLLM()
        self.engine = RelationshipEngine(self.llm)

    def test_new_relationship_returns_none(self):
        self.assertIsNone(self.engine.get_relationship("x", "y"))

    def test_create_relationship(self):
        profile = self.engine.get_or_create_relationship("x", "y")
        self.assertIsNotNone(profile)
        self.assertEqual(profile.agent_a, "x")
        self.assertEqual(profile.agent_b, "y")

    def test_key_normalization(self):
        p1 = self.engine.get_or_create_relationship("alice", "bob")
        p2 = self.engine.get_or_create_relationship("bob", "alice")
        self.assertIs(p1, p2)

    def test_record_interaction_returns_result(self):
        result = self.engine.record_interaction(
            "alice", "bob", {"action": "help", "result": "success", "narrative": "帮助"}
        )
        self.assertIn("balance", result)
        self.assertIn("relation_type", result)
        self.assertIn("impact", result)
        self.assertIn("interaction_count", result)

    def test_average_balance(self):
        self.engine.record_interaction(
            "a", "b", {"action": "help", "result": "success", "narrative": ""}
        )
        self.engine.record_interaction(
            "c", "d", {"action": "help", "result": "success", "narrative": ""}
        )
        avg = self.engine.average_balance()
        self.assertGreater(avg, 0.0)
        self.assertLess(avg, 1.0)

    def test_empty_average_balance(self):
        self.assertEqual(self.engine.average_balance(), 0.5)

    def test_can_cooperate_no_history(self):
        result = self.engine.can_cooperate("x", "y")
        self.assertTrue(result["can"])
        self.assertEqual(result["reason"], "无历史交互")

    def test_can_cooperate_good_relationship(self):
        # 建立良好关系
        for _ in range(5):
            self.engine.record_interaction(
                "x", "y", {"action": "help", "result": "success", "narrative": ""}
            )
        result = self.engine.can_cooperate("x", "y")
        self.assertTrue(result["can"])

    def test_all_agent_ids(self):
        self.engine.record_interaction(
            "alice", "bob", {"action": "help", "result": "success", "narrative": ""}
        )
        self.engine.record_interaction(
            "carol", "dave", {"action": "help", "result": "success", "narrative": ""}
        )
        ids = self.engine.all_agent_ids()
        self.assertEqual(set(ids), {"alice", "bob", "carol", "dave"})

    def test_moloch_no_zones_healthy(self):
        # 全部良性交互
        for _ in range(5):
            self.engine.record_interaction(
                "a", "b", {"action": "help", "result": "success", "narrative": ""}
            )
        report = self.engine.get_moloch_report()
        self.assertFalse(report["active"])
        self.assertEqual(len(report["zones"]), 0)


# ═══════════════════════════════════════════════════════════════
# RelationshipTracker
# ═══════════════════════════════════════════════════════════════


class TestRelationshipTracker(unittest.TestCase):
    def setUp(self):
        self.tracker = RelationshipTracker()

    def test_default_mock(self):
        self.assertEqual(self.tracker.llm_type, "MockLLM")

    def test_track_returns_result(self):
        result = self.tracker.track(
            "alice", "bob",
            {"action": "help", "result": "success", "narrative": "alice 帮助了 bob"},
        )
        self.assertIsInstance(result.balance, float)
        self.assertIsInstance(result.relation_type, str)
        self.assertGreater(result.balance, 0.0)

    def test_health_no_history(self):
        h = self.tracker.health("x", "y")
        self.assertIsNone(h)

    def test_health_returns_correct_fields(self):
        self.tracker.track(
            "alice", "bob",
            {"action": "help", "result": "success", "narrative": ""},
        )
        h = self.tracker.health("alice", "bob")
        self.assertIsNotNone(h)
        self.assertIn(h.risk, ["low", "medium", "high", "critical"])
        self.assertGreaterEqual(h.balance, 0.0)
        self.assertLessEqual(h.balance, 1.0)

    def test_network_report(self):
        self.tracker.track(
            "alice", "bob",
            {"action": "help", "result": "success", "narrative": ""},
        )
        report = self.tracker.network(["alice", "bob"])
        self.assertEqual(report.agent_count, 2)
        self.assertEqual(report.relationship_count, 1)
        self.assertIsNotNone(report.pairs.get(("alice", "bob")))

    def test_network_heatmap(self):
        self.tracker.track(
            "alice", "bob",
            {"action": "help", "result": "success", "narrative": ""},
        )
        report = self.tracker.network(["alice", "bob"])
        hm = report.heatmap()
        self.assertIn("alice", hm)
        self.assertIn("bob", hm)

    def test_network_all_agents(self):
        self.tracker.track(
            "a", "b", {"action": "help", "result": "success", "narrative": ""},
        )
        report = self.tracker.network()
        self.assertEqual(report.agent_count, 2)

    def test_detect_moloch_healthy(self):
        for _ in range(5):
            self.tracker.track(
                "a", "b",
                {"action": "help", "result": "success", "narrative": ""},
            )
        moloch = self.tracker.detect_moloch()
        self.assertFalse(moloch.active)

    def test_repair_paths(self):
        paths = self.tracker.repair_paths("x", "y")
        self.assertEqual(len(paths), 3)
        self.assertEqual(paths[0].cost, "low")

    def test_summary(self):
        self.tracker.track(
            "alice", "bob",
            {"action": "help", "result": "success", "narrative": ""},
        )
        s = self.tracker.summary()
        self.assertIn("Agent 关系健康摘要", s)
        self.assertIn("alice", s)
        self.assertIn("bob", s)

    def test_multiple_interactions_change_balance(self):
        """多次交互应该改变 balance"""
        result1 = self.tracker.track(
            "alice", "bob",
            {"action": "help", "result": "success", "narrative": "帮助"},
        )
        # 做一次有害交互
        result2 = self.tracker.track(
            "alice", "bob",
            {"action": "conflict", "result": "failed", "narrative": "冲突"},
        )
        # balance 应该向负方向移动
        self.assertLess(result2.balance, result1.balance)

    def test_track_has_risk_and_transition(self):
        """v0.2.0: TrackResult 包含 risk 和 risk_transition"""
        # 先建立良好关系
        for _ in range(4):
            self.tracker.track(
                "alice", "bob",
                {"action": "help", "result": "success", "narrative": "帮助"},
            )
        # 做一次冲突
        result = self.tracker.track(
            "alice", "bob",
            {"action": "conflict", "result": "failed", "narrative": "冲突"},
        )
        # 应该有风险信息
        self.assertIn(result.risk, ["low", "medium", "high", "critical"])
        # 应该有前值
        self.assertIsNotNone(result.prev_balance)

    def test_can_cooperate_no_history(self):
        """v0.2.0: can_cooperate 对无历史返回 true"""
        result = self.tracker.can_cooperate("x", "y")
        self.assertTrue(result["can"])
        self.assertFalse(result["needs_repair"])

    def test_can_cooperate_with_repair_attempt(self):
        """v0.2.0: can_cooperate 支持修复尝试"""
        # 建立很差的关���
        for _ in range(10):
            self.tracker.track(
                "x", "y",
                {"action": "conflict", "result": "failed", "narrative": "持续冲突"},
            )
        h = self.tracker.health("x", "y")
        if h and h.balance < 0.4:
            result = self.tracker.can_cooperate("x", "y")
            self.assertFalse(result["can"])
            self.assertTrue(result["needs_repair"])

            # 尝试修复
            repair_result = self.tracker.can_cooperate(
                "x", "y", try_repair=True
            )
            self.assertTrue(repair_result["can"])

    def test_history_empty(self):
        """v0.2.0: 无历史返回空列表"""
        hist = self.tracker.history("x", "y")
        self.assertEqual(hist, [])

    def test_history_returns_records(self):
        """v0.2.0: history 返回交互记录"""
        self.tracker.track(
            "alice", "bob",
            {"action": "help", "result": "success", "narrative": "帮助"},
        )
        hist = self.tracker.history("alice", "bob")
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0]["action"], "help")
        self.assertIn("impact_a", hist[0])

    def test_on_risk_change_callback(self):
        """v0.2.0: 风险跨越阈值时触发回调"""
        calls = []

        def on_risk_change(a, b, old_risk, new_risk, balance):
            calls.append((a, b, old_risk, new_risk, balance))

        tracker = RelationshipTracker(on_risk_change=on_risk_change)

        # 先建立良好关系 (让 risk_cache 记住 low)
        for _ in range(3):
            tracker.track(
                "alice", "bob",
                {"action": "help", "result": "success", "narrative": "互相帮助"},
            )

        # 用明确的攻击行为恶化关系
        for _ in range(8):
            tracker.track(
                "alice", "bob",
                {"action": "attack", "result": "failed", "narrative": "攻击破坏"},
            )

        # 应该至少有一次风险过渡回调
        self.assertGreater(len(calls), 0, f"calls={calls}")
        # 最后一个回调应该是向更差方向
        risk_order = ["low", "medium", "high", "critical"]
        last = calls[-1]
        self.assertGreater(
            risk_order.index(last[3]), risk_order.index(last[2]),
            f"expected worsening, got {last[2]}→{last[3]}"
        )

    def test_moloch_typed_zones(self):
        """v0.2.0: MolochZone 是类型化对象"""
        moloch = self.tracker.detect_moloch()
        self.assertIsInstance(moloch.zones, list)
        # 即使为空，每个 zone 应该有类型
        for z in moloch.zones:
            self.assertTrue(hasattr(z, "agents"))
            self.assertTrue(hasattr(z, "severity"))
            self.assertTrue(hasattr(z, "trend"))


# ═══════════════════════════════════════════════════════════════
# LLM Backend
# ═══════════════════════════════════════════════════════════════


class TestLLMBackend(unittest.TestCase):
    def test_mock_estimate_impact_beneficial(self):
        llm = MockLLM()
        impact = llm.estimate_impact(
            "alice", {"action": "help", "result": "成功帮助"}, True
        )
        self.assertGreater(impact, 0)

    def test_mock_estimate_impact_harmful(self):
        llm = MockLLM()
        impact = llm.estimate_impact(
            "alice", {"action": "attack", "result": "破坏了系统"}, True
        )
        self.assertLess(impact, 0)

    def test_mock_chat_json_mode(self):
        llm = MockLLM()
        result = llm.chat("test", json_mode=True)
        self.assertIn("result", result)

    def test_fallback_beneficial(self):
        impact = LLMBackend._fallback_estimate_impact(
            {"action": "help"}, True
        )
        self.assertGreater(impact, 0)

    def test_fallback_harmful(self):
        impact = LLMBackend._fallback_estimate_impact(
            {"action": "attack"}, True
        )
        self.assertLess(impact, 0)


# ═══════════════════════════════════════════════════════════════
# 回归测试 — v0.2.0 审查修复
# ═══════════════════════════════════════════════════════════════


class TestRegressionFixes(unittest.TestCase):
    """针对代码审查发现的 Bug 的回归测试"""

    def test_risk_cache_normalized_key(self):
        """B2: track("a","b") 后 track("b","a") 风险状态应共享"""
        calls = []

        def on_risk(a, b, old, new, bal):
            calls.append((a, b, old, new))

        tracker = RelationshipTracker(on_risk_change=on_risk)

        # 用 alice→bob 建立良好关系
        for _ in range(4):
            tracker.track("alice", "bob", {
                "action": "help", "result": "success", "narrative": "帮助"
            })

        # 用 bob→alice (反序) 做冲突
        result = tracker.track("bob", "alice", {
            "action": "attack", "result": "failed", "narrative": "攻击"
        })

        # 反序调用应该能识别到之前的风险状态
        # 如果 key 未规范化，prev_risk 会是 None，transition 会是 None
        # 修复后应该能拿到之前的 risk
        self.assertIsNotNone(result.risk_transition)

    def test_empty_network_heatmap(self):
        """B3: 空 pairs 的 heatmap 不应崩溃"""
        from agent_relationship.types import NetworkReport

        report = NetworkReport(
            pairs={},
            avg_balance=0.5,
            weakest_link=None,
            agent_count=0,
            relationship_count=0,
        )
        hm = report.heatmap()
        self.assertIn("无关系数据", hm)

    def test_max_history_applied(self):
        """B1: max_history 参数应实际生效"""
        tracker = RelationshipTracker(max_history=5)

        for _ in range(10):
            tracker.track("a", "b", {
                "action": "help", "result": "success", "narrative": ""
            })

        profile = tracker.engine.get_relationship("a", "b")
        self.assertEqual(len(profile.interaction_history), 5)

    def test_repair_paths_filters_extreme(self):
        """R3: balance 极低时 repair_paths 应过滤低成功率路径"""
        tracker = RelationshipTracker()

        # 正常情况: 3 条路径
        paths_normal = tracker.repair_paths("x", "y")
        self.assertEqual(len(paths_normal), 3)

        # 极度恶化: 应过滤掉 success_probability < 0.6 的
        for _ in range(15):
            tracker.track("x", "y", {
                "action": "attack", "result": "failed", "narrative": "攻击破坏"
            })

        h = tracker.health("x", "y")
        if h and h.balance < 0.15:
            paths_extreme = tracker.repair_paths("x", "y")
            self.assertLessEqual(len(paths_extreme), 2)
            for p in paths_extreme:
                self.assertGreaterEqual(p.success_probability, 0.6)

    def test_mock_no_dead_code_chaoe(self):
        """B4: '超额' 不应在 advantageous 中 (已被 unbalanced 优先匹配)"""
        llm = MockLLM()
        # "超额" 场景 → 应该走 unbalanced (impact=-0.15)，不是 advantageous (impact=0.6)
        impact = llm.estimate_impact(
            "test", {"action": "exchange", "narrative": "超额提取"}, True
        )
        self.assertLess(impact, 0, "'超额' 应被识别为 unbalanced (负影响)")


if __name__ == "__main__":
    unittest.main()
