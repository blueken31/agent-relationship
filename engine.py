"""
Agent Relationship — 关系引擎

管理所有 Agent 之间的关系场：记录交互、评估健康、检测危机。
"""

import time
from typing import Dict, List, Optional, Set, Tuple

from .models import RelationProfile, InteractionRecord
from .repair import RepairMechanism
from .llm import LLMBackend


class RelationshipEngine:
    """
    关系引擎 — 多 Agent 系统的社会层。

    核心功能:
    - 记录交互 → 更新 balance / trust / type
    - 查询双边关系健康度
    - Moloch 竞争升级检测 (BFS 连通分量 + 趋势)
    - 关系修复路径
    """

    def __init__(self, llm: LLMBackend, max_history: int = 100):
        self.llm = llm
        self.profiles: Dict[Tuple[str, str], RelationProfile] = {}
        self.repair = RepairMechanism()
        self.moloch_threshold: float = 0.3
        self.moloch_min_zone_size: int = 3
        self._previous_moloch_zones: List[Dict] = []
        self.max_history = max_history

    def _key(self, a: str, b: str) -> Tuple[str, str]:
        """规范化键 — (a,b) 和 (b,a) 指向同一 profile"""
        return (a, b) if a < b else (b, a)

    # ── 查询 ──

    def get_relationship(
        self, agent_a: str, agent_b: str
    ) -> Optional[RelationProfile]:
        """查询双边关系"""
        return self.profiles.get(self._key(agent_a, agent_b))

    def get_or_create_relationship(
        self, agent_a: str, agent_b: str
    ) -> RelationProfile:
        """查询或创建双边关系"""
        key = self._key(agent_a, agent_b)
        if key not in self.profiles:
            self.profiles[key] = RelationProfile(
                agent_a=key[0], agent_b=key[1],
                max_history_length=self.max_history,
            )
        return self.profiles[key]

    # ── 记录交互 ──

    def record_interaction(
        self,
        initiator: str,
        target: str,
        interaction_data: Dict,
    ) -> Dict:
        """
        记录一次交互，更新关系档案。

        interaction_data 自由格式:
          {"action": "...", "result": "...", "narrative": "..."}
        """
        impact_a = self._estimate_impact(
            initiator, interaction_data, True
        )
        impact_b = self._estimate_impact(
            target, interaction_data, False
        )

        record = InteractionRecord(
            timestamp=time.time(),
            interaction_type=interaction_data.get(
                "action", interaction_data.get("type", "unknown")
            ),
            initiator=initiator,
            target=target,
            action=interaction_data,
            impact_a=impact_a,
            impact_b=impact_b,
            context=interaction_data,
        )

        profile = self.get_or_create_relationship(initiator, target)
        profile.record_interaction(record)

        return {
            "balance": profile.balance,
            "trust": profile.trust,
            "relation_type": profile.relation_type.value,
            "impact": (impact_a + impact_b) / 2,
            "interaction_count": profile.interaction_count,
        }

    # ── 协作检查 ──

    def can_cooperate(
        self,
        agent_a: str,
        agent_b: str,
        threshold: float = 0.4,
        is_repair_attempt: bool = False,
    ) -> Dict:
        """
        检查两个 Agent 是否可以协作。

        不是简单的是/否 — 返回修复路径而非永久封锁。
        """
        profile = self.get_relationship(agent_a, agent_b)

        if not profile:
            return {
                "can": True,
                "reason": "无历史交互",
                "confidence": 0.3,
                "needs_repair": False,
            }

        if is_repair_attempt:
            can_repair, msg = self.repair.can_attempt_repair(profile)
            if can_repair:
                result = self.repair.execute_repair_attempt(
                    profile
                )
                return {
                    "can": True,
                    "reason": f"修复尝试: {msg}",
                    "confidence": result["temp_threshold"],
                    "needs_repair": True,
                }
            else:
                return {
                    "can": False,
                    "reason": msg,
                    "confidence": 0.0,
                    "needs_repair": True,
                }

        if profile.balance > threshold:
            return {
                "can": True,
                "reason": f"关系良好 (balance={profile.balance:.2f})",
                "confidence": profile.balance,
                "needs_repair": False,
            }
        else:
            return {
                "can": False,
                "reason": (
                    f"关系需要修复 (balance={profile.balance:.2f})"
                ),
                "confidence": 1.0 - profile.balance,
                "needs_repair": True,
                "repair_paths": [
                    p["description"]
                    for p in self.repair.available_paths()
                ],
            }

    # ── 影响评估 ──

    def _estimate_impact(
        self, agent_id: str, interaction: Dict, is_initiator: bool
    ) -> float:
        """通过 LLM 估算交互影响"""
        return self.llm.estimate_impact(
            agent_id, interaction, is_initiator
        )

    # ── Moloch 检测 ──

    def _detect_moloch_zones(self) -> List[Dict]:
        """
        Moloch 萌芽检测。

        1. 收集 balance < threshold 的边
        2. BFS 找连通分量
        3. 过滤 ≥ min_zone_size 的分量
        4. 计算平均 balance / 严重程度 / 趋势
        """
        threshold = self.moloch_threshold
        min_size = self.moloch_min_zone_size

        imbalanced_edges = [
            (a, b)
            for (a, b), profile in self.profiles.items()
            if profile.balance < threshold
            and profile.interaction_count >= 3
        ]

        # 建图
        graph: Dict[str, Set[str]] = {}
        for a, b in imbalanced_edges:
            graph.setdefault(a, set()).add(b)
            graph.setdefault(b, set()).add(a)

        moloch_zones = []
        visited: Set[str] = set()

        for node in graph:
            if node in visited:
                continue

            component: Set[str] = set()
            queue = [node]
            while queue:
                current = queue.pop(0)
                if current in visited:
                    continue
                visited.add(current)
                component.add(current)
                for neighbor in graph.get(current, set()):
                    if neighbor not in visited:
                        queue.append(neighbor)

            if len(component) >= min_size:
                zone_balances: List[float] = []
                for a, b in imbalanced_edges:
                    if a in component and b in component:
                        profile = self.profiles.get((a, b))
                        if profile:
                            zone_balances.append(profile.balance)

                avg_balance = (
                    sum(zone_balances) / len(zone_balances)
                    if zone_balances
                    else 0.0
                )

                trend = "stable"
                prev = self._find_previous_zone(component)
                if prev:
                    delta = avg_balance - prev["avg_balance"]
                    if delta < -0.05:
                        trend = "deteriorating"
                    elif delta > 0.05:
                        trend = "improving"

                moloch_zones.append(
                    {
                        "agents": sorted(component),
                        "size": len(component),
                        "avg_balance": avg_balance,
                        "trend": trend,
                        "severity": self._classify_severity(
                            avg_balance, len(component), trend
                        ),
                        "first_detected": prev is None,
                    }
                )

        self._previous_moloch_zones = moloch_zones
        return moloch_zones

    def get_moloch_report(self) -> Dict:
        """获取 Moloch 检测报告"""
        zones = self._detect_moloch_zones()
        total = len(self.profiles)
        balanced = sum(
            1
            for p in self.profiles.values()
            if p.balance >= self.moloch_threshold
        )
        return {
            "zones": zones,
            "active": len(zones) > 0,
            "total_relationships": total,
            "balanced_ratio": balanced / max(total, 1),
        }

    def _find_previous_zone(
        self, component: Set[str]
    ) -> Optional[Dict]:
        comp_set = set(component)
        for prev in self._previous_moloch_zones:
            if set(prev["agents"]) == comp_set:
                return prev
        return None

    @staticmethod
    def _classify_severity(
        avg_balance: float, size: int, trend: str
    ) -> str:
        if avg_balance < 0.1:
            base = "critical"
        elif avg_balance < 0.2:
            base = "high"
        elif avg_balance < 0.3:
            base = "moderate"
        else:
            base = "watch"

        if trend == "deteriorating" and base in ("moderate", "high"):
            return f"{base}_escalating"
        if size >= 10 and trend == "deteriorating":
            return f"{base}_large_scale"

        return base

    # ── 聚合 ──

    def average_balance(self) -> float:
        """全局平均 balance"""
        if not self.profiles:
            return 0.5
        return (
            sum(p.balance for p in self.profiles.values())
            / len(self.profiles)
        )

    def all_agent_ids(self) -> List[str]:
        """获取所有已知的 Agent ID"""
        ids: Set[str] = set()
        for a, b in self.profiles:
            ids.add(a)
            ids.add(b)
        return sorted(ids)
