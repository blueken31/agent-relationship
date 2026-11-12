"""Relationship evidence and descriptive (not authorization) scores.

Balance summarizes recent assessed interactions. It is not a probability or a
substitute for checking unresolved incidents and the proposed task's permissions.
"""

from dataclasses import dataclass, field
from enum import Enum
import math
import time
from typing import Dict, List, Optional


class RelationType(Enum):
    """Observed impact patterns, independently of the balance score.

    COMPETITIVE is retained for compatibility; competition cannot be established
    from two impact numbers alone and is not inferred by this model.
    """

    SYMBIOTIC = "symbiotic"
    COMMENSAL = "commensal"
    PARASITIC = "parasitic"
    COMPETITIVE = "competitive"
    CONFLICTUAL = "conflictual"
    NEUTRAL = "neutral"


@dataclass
class InteractionRecord:
    """One event; impacts always refer to initiator and target, respectively.

    Unknown assessments may have missing impacts. Their numerical values, if any,
    are never used to update relationship scores.
    """

    timestamp: float
    interaction_type: str
    initiator: str
    target: str
    action: Dict = field(default_factory=dict)
    impact_a: Optional[float] = 0.0
    impact_b: Optional[float] = 0.0
    context: Dict = field(default_factory=dict)
    event_id: str = ""
    task_id: Optional[str] = None
    assessment_status: str = "assessed"
    assessment_source: str = "provided"
    assessment_reason: str = ""
    assessment_error: Optional[str] = None
    goal_status: str = "unknown"

    def __post_init__(self) -> None:
        if self.assessment_status not in {"assessed", "unknown"}:
            raise ValueError("assessment_status must be assessed or unknown")
        if self.goal_status not in {"met", "violated", "unknown"}:
            raise ValueError("goal_status must be met, violated or unknown")
        for name in ("impact_a", "impact_b"):
            value = getattr(self, name)
            if value is None and self.assessment_status == "unknown":
                continue
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not -1.0 <= value <= 1.0
                or not math.isfinite(value)
            ):
                raise ValueError(f"{name} must be a finite number between -1 and 1")
            setattr(self, name, float(value))

    @property
    def is_assessed(self) -> bool:
        return self.assessment_status == "assessed"

    @property
    def net_impact(self) -> Optional[float]:
        """Descriptive total only; gains never offset losses in the score."""
        if not self.is_assessed:
            return None
        return self.impact_a + self.impact_b

    @property
    def is_symbiotic(self) -> bool:
        return (
            self.is_assessed
            and self.goal_status != "violated"
            and self.impact_a > 0
            and self.impact_b > 0
        )

    @property
    def is_parasitic(self) -> bool:
        return (
            self.is_assessed
            and self.goal_status != "violated"
            and ((self.impact_a > 0 > self.impact_b)
                 or (self.impact_b > 0 > self.impact_a))
        )

    @property
    def is_conflictual(self) -> bool:
        """Harm without a beneficiary, or an assessed task-goal violation."""
        return self.is_assessed and (
            self.goal_status == "violated"
            or (min(self.impact_a, self.impact_b) < 0
                and max(self.impact_a, self.impact_b) <= 0)
        )

    @property
    def impact_pattern(self) -> RelationType:
        if not self.is_assessed:
            return RelationType.NEUTRAL
        if self.is_conflictual:
            return RelationType.CONFLICTUAL
        if self.is_parasitic:
            return RelationType.PARASITIC
        if self.is_symbiotic:
            return RelationType.SYMBIOTIC
        if max(self.impact_a, self.impact_b) > 0:
            return RelationType.COMMENSAL
        return RelationType.NEUTRAL

    @property
    def impact_score(self) -> Optional[float]:
        """Monotone loss score: a beneficiary cannot cancel another's harm."""
        if not self.is_assessed:
            return None
        losses = -min(self.impact_a, 0.0) - min(self.impact_b, 0.0)
        if self.goal_status == "violated":
            return -1.5 * max(1.0, losses)
        if losses:
            multiplier = 1.5 if self.impact_a < 0 and self.impact_b < 0 else 1.0
            return -losses * multiplier
        return self.impact_a + self.impact_b


@dataclass
class RelationProfile:
    """A pair-level overview; task-specific incidents are tracked separately."""

    agent_a: str
    agent_b: str
    created_at: float = field(default_factory=time.time)
    balance: float = 0.5
    trust: float = 0.3
    relation_type: RelationType = RelationType.NEUTRAL
    interaction_count: int = 0
    symbiotic_count: int = 0
    parasitic_count: int = 0
    conflictual_count: int = 0
    last_interaction_time: float = 0.0
    interaction_history: List[InteractionRecord] = field(default_factory=list)
    max_history_length: int = 100
    mutual_benefit_ratio: float = 0.5
    power_asymmetry: float = 0.0
    repair_attempts: int = 0
    last_repair_time: float = 0.0
    assessed_count: int = 0

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_history_length, bool)
            or not isinstance(self.max_history_length, int)
            or self.max_history_length <= 0
        ):
            raise ValueError("max_history_length must be a positive integer")

    @property
    def confidence(self) -> float:
        """Evidence-count indicator, not a calibrated success probability."""
        return self.assessed_count / (self.assessed_count + 5.0)

    def _assessed_history(self) -> List[InteractionRecord]:
        return [record for record in self.interaction_history if record.is_assessed]

    def _recalculate_balance(self) -> None:
        """Exponential weighting by assessed-event order, not elapsed time.

        Unknown events are excluded from both weighting and normalization. If the
        retained history contains none, preserve the last known score; unknown
        evidence cannot turn a previous bad relationship into a neutral one.
        """
        records = self._assessed_history()
        if not records:
            return
        weighted_sum = 0.0
        total_weight = 0.0
        for i, record in enumerate(records):
            weight = math.exp(-0.05 * (len(records) - 1 - i))
            weighted_sum += record.impact_score * weight
            total_weight += weight
        avg_impact = weighted_sum / total_weight
        self.balance = 1.0 / (1.0 + math.exp(-avg_impact * 3.0))

    def _update_trust(self, record: InteractionRecord) -> None:
        """Negative evidence never raises trust; unknown/neutral leaves it alone."""
        if not record.is_assessed or record.impact_score == 0:
            return
        if record.impact_score < 0:
            self.trust = max(
                0.0,
                min(self.trust - 0.1, self.trust * 0.7 + self.balance * 0.3),
            )
        else:
            self.trust = min(
                1.0,
                max(self.trust, (self.trust + 0.05) * 0.7 + self.balance * 0.3),
            )

    def _update_relation_type(self) -> None:
        """Choose the dominant recent impact pattern; score is a separate axis."""
        records = self._assessed_history()
        if not records:
            return
        patterns = {kind: 0.0 for kind in RelationType}
        asymmetry = 0.0
        magnitude = 0.0
        for i, record in enumerate(records):
            weight = math.exp(-0.05 * (len(records) - 1 - i))
            patterns[record.impact_pattern] += weight
            # Record impacts refer to direction, not the canonical pair order.
            a, b = (record.impact_a, record.impact_b)
            if record.initiator != self.agent_a:
                a, b = b, a
            asymmetry += (a - b) * weight
            magnitude += (abs(a) + abs(b)) * weight
        self.relation_type = max(patterns, key=patterns.get)
        self.power_asymmetry = (
            min(1.0, abs(asymmetry) / magnitude) if magnitude else 0.0
        )

    def record_interaction(self, record: InteractionRecord) -> None:
        """Retain evidence and update assessed statistics without rewarding gaps."""
        if {record.initiator, record.target} != {self.agent_a, self.agent_b}:
            raise ValueError("interaction participants do not match this relationship")
        self.interaction_history.append(record)
        if len(self.interaction_history) > self.max_history_length:
            self.interaction_history.pop(0)
        self.interaction_count += 1
        self.last_interaction_time = record.timestamp
        if not record.is_assessed:
            return
        self.assessed_count += 1
        if record.is_symbiotic:
            self.symbiotic_count += 1
        if record.is_parasitic:
            self.parasitic_count += 1
        if record.is_conflictual:
            self.conflictual_count += 1
        self.mutual_benefit_ratio = self.symbiotic_count / self.assessed_count
        self._recalculate_balance()
        self._update_trust(record)
        self._update_relation_type()
