"""
Agent Relationship — 核心数据模型

RelationType, InteractionRecord, RelationProfile
"""

from dataclasses import dataclass, field
from enum import Enum
import time
import math
from typing import Dict, List


class RelationType(Enum):
    """关系类型 — 从共生到冲突的六级光谱"""

    SYMBIOTIC = "symbiotic"
    COMMENSAL = "commensal"
    PARASITIC = "parasitic"
    COMPETITIVE = "competitive"
    CONFLICTUAL = "conflictual"
    NEUTRAL = "neutral"


# ── InteractionRecord ──


@dataclass
class InteractionRecord:
    """一次交互的完整记录"""

    timestamp: float
    interaction_type: str
    initiator: str
    target: str
    action: Dict = field(default_factory=dict)
    impact_a: float = 0.0  # 对发起方的影响 (-1~+1)
    impact_b: float = 0.0  # 对接收方的影响 (-1~+1)
    context: Dict = field(default_factory=dict)

    @property
    def net_impact(self) -> float:
        """双方净影响之和"""
        return self.impact_a + self.impact_b

    @property
    def is_symbiotic(self) -> bool:
        """双方都受益 → 共生"""
        return self.impact_a > 0 and self.impact_b > 0

    @property
    def is_parasitic(self) -> bool:
        """一方受益一方受损 → 寄生"""
        return (self.impact_a > 0 > self.impact_b) or (
            self.impact_b > 0 > self.impact_a
        )

    @property
    def is_conflictual(self) -> bool:
        """双方都受损 → 冲突"""
        return self.impact_a < 0 and self.impact_b < 0


# ── RelationProfile ──


@dataclass
class RelationProfile:
    """两个 Agent 之间的完整关系画像"""

    agent_a: str
    agent_b: str
    created_at: float = field(default_factory=time.time)

    # 核心指标
    balance: float = 0.5  # "舞蹈平衡度" — 关系的核心健康指标
    trust: float = 0.3
    relation_type: RelationType = RelationType.NEUTRAL

    # 交互统计
    interaction_count: int = 0
    symbiotic_count: int = 0
    parasitic_count: int = 0
    conflictual_count: int = 0
    last_interaction_time: float = 0.0

    # 历史
    interaction_history: List[InteractionRecord] = field(default_factory=list)
    max_history_length: int = 100

    # 衍生指标
    mutual_benefit_ratio: float = 0.5
    power_asymmetry: float = 0.0

    # 修复
    repair_attempts: int = 0
    last_repair_time: float = 0.0

    # ── 核心算法 ──

    def _recalculate_balance(self) -> None:
        """
        dance_balance 计算 — 指数衰减加权。

        越新的交互权重越高: weight = e^(-0.05 * (N-1-i))
        - 最新交互: weight = 1.0
        - 最旧交互: weight → 0
        """
        records = self.interaction_history
        N = len(records)

        if N == 0:
            self.balance = 0.5
            return

        decayed_sum = 0.0
        total_weight = 0.0

        for i, record in enumerate(records):
            weight = math.exp(-0.05 * (N - 1 - i))

            if record.is_symbiotic:
                impact_score = record.net_impact
            elif record.is_parasitic:
                impact_score = -abs(record.net_impact)
            elif record.is_conflictual:
                impact_score = -abs(record.net_impact) * 1.5  # 冲突惩罚
            else:
                impact_score = 0.0

            decayed_sum += impact_score * weight
            total_weight += weight

        avg_impact = decayed_sum / max(total_weight, 0.001)
        self.balance = 1.0 / (1.0 + math.exp(-avg_impact * 3))
        self.balance = max(0.0, min(1.0, self.balance))

    def _update_trust(self, record: InteractionRecord) -> None:
        """信任度更新 — 趋同于 balance"""
        if record.is_symbiotic:
            self.trust = min(1.0, self.trust + 0.05)
        elif record.is_parasitic or record.is_conflictual:
            self.trust = max(0.0, self.trust - 0.1)
        self.trust = self.trust * 0.7 + self.balance * 0.3

    def _update_relation_type(self) -> None:
        """根据 balance 更新关系类型"""
        if self.balance >= 0.7:
            self.relation_type = RelationType.SYMBIOTIC
        elif self.balance >= 0.5:
            self.relation_type = RelationType.COMMENSAL
        elif self.balance >= 0.4:
            self.relation_type = RelationType.NEUTRAL
        elif self.balance >= 0.3:
            self.relation_type = RelationType.COMPETITIVE
        elif self.balance >= 0.15:
            self.relation_type = RelationType.PARASITIC
        else:
            self.relation_type = RelationType.CONFLICTUAL

        if self.interaction_count >= 5:
            asym = (
                abs(self.symbiotic_count - self.parasitic_count)
                / max(self.interaction_count, 1)
            )
            self.power_asymmetry = min(1.0, asym * 3)

    def record_interaction(self, record: InteractionRecord) -> None:
        """记录一次交互，触发全面更新"""
        self.interaction_history.append(record)
        if len(self.interaction_history) > self.max_history_length:
            self.interaction_history.pop(0)

        self.interaction_count += 1
        if record.is_symbiotic:
            self.symbiotic_count += 1
        if record.is_parasitic:
            self.parasitic_count += 1
        if record.is_conflictual:
            self.conflictual_count += 1

        self.last_interaction_time = record.timestamp

        if self.interaction_count > 0:
            self.mutual_benefit_ratio = (
                self.symbiotic_count / self.interaction_count
            )

        self._recalculate_balance()
        self._update_trust(record)
        self._update_relation_type()
