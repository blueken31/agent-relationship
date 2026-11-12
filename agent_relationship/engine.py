"""
Agent Relationship — 关系引擎

管理所有 Agent 之间的关系场：记录交互、评估健康、检测危机。
"""

import time
import copy
import json
import math
import threading
import uuid
from typing import Dict, List, Optional, Set, Tuple

from .models import RelationProfile, InteractionRecord
from .repair import RepairMechanism
from .llm import LLMBackend
from .recovery import RecoveryLedger


class RelationshipEngine:
    """
    关系引擎 — 多 Agent 系统的社会层。

    核心功能:
    - 记录交互 → 更新 balance / trust / type
    - 查询双边关系健康度
    - Moloch 竞争升级检测 (BFS 连通分量 + 趋势)
    - 关系修复路径
    """

    def __init__(self, llm: LLMBackend, max_history: int = 100, temperature: float = 0.3,
                 diagnosis_policy: str = "legacy"):
        if isinstance(max_history, bool) or not isinstance(max_history, int) or max_history < 1:
            raise ValueError("max_history 必须为正整数")
        if isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or not math.isfinite(temperature) or not 0 <= temperature <= 2:
            raise ValueError("temperature 必须为 0 到 2 的有限数值")
        self.llm = llm
        self.temperature = temperature
        self.lock = threading.RLock()
        self.recovery = RecoveryLedger(diagnosis_policy=diagnosis_policy)
        self._task_profiles = {}
        self._events = {}
        self.event_records = {}
        self._zones = []
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
        self, agent_a: str, agent_b: str, task_id: Optional[str] = None
    ) -> Optional[RelationProfile]:
        """查询双边关系"""
        if task_id is not None:
            return self._task_profiles.get((task_id, *self._key(agent_a, agent_b)))
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

    @staticmethod
    def risk_for(profile: RelationProfile) -> str:
        if not profile.assessed_count or (profile.interaction_history and
                profile.interaction_history[-1].assessment_status != "assessed"):
            return "unknown"
        return ("low" if profile.balance >= 0.7 else "medium" if profile.balance >= 0.5
                else "high" if profile.balance >= 0.3 else "critical")

    def record_interaction(self, initiator: str, target: str, interaction_data: Dict) -> Dict:
        """Atomically record a JSON event. Explicit event IDs are idempotent per instance."""
        if any(not isinstance(a, str) or not a.strip() for a in (initiator, target)) or initiator == target:
            raise ValueError("需要两个不同且非空的 Agent ID")
        if not isinstance(interaction_data, dict):
            raise ValueError("interaction 必须是 JSON 对象")
        try:
            data = json.loads(json.dumps(interaction_data, ensure_ascii=False, allow_nan=False))
        except (ValueError, TypeError, RecursionError) as exc:
            raise ValueError("interaction 必须包含可序列化的有限 JSON 值") from exc
        event_id = data.get("event_id", str(uuid.uuid4()))
        task_id = data.get("task_id")
        if not isinstance(event_id, str) or not event_id.strip():
            raise ValueError("event_id 必须为非空字符串")
        if task_id is not None and (not isinstance(task_id, str) or not task_id.strip()):
            raise ValueError("task_id 必须为非空字符串")
        action = data.get("action", data.get("type", "unknown"))
        if not isinstance(action, str) or not action.strip():
            raise ValueError("action 必须为非空字符串")
        fingerprint = json.dumps([initiator, target, data], sort_keys=True, ensure_ascii=False)
        if self.recovery.diagnosis_policy == "task_evidence_v1":
            from .evidence_policy import validate_context
            validate_context(data)
        with self.lock:
            if event_id in self._events:
                previous_fingerprint, previous = self._events[event_id]
                if previous_fingerprint != fingerprint:
                    raise ValueError("event_id 已被不同内容使用")
                result = copy.deepcopy(previous)
                result["duplicate"] = True
                return result
            assessment = self.llm.assess_interaction(initiator, target, data, temperature=self.temperature)
            pair_key = self._key(initiator, target)
            original = self.get_relationship(initiator, target)
            profile = copy.deepcopy(original) if original else RelationProfile(
                agent_a=pair_key[0], agent_b=pair_key[1], max_history_length=self.max_history)
            prev_balance = profile.balance if profile.interaction_count else None
            record = InteractionRecord(
                timestamp=time.time(), interaction_type=action, initiator=initiator, target=target,
                action=copy.deepcopy(data), context=copy.deepcopy(data),
                impact_a=assessment.impact_a, impact_b=assessment.impact_b,
                event_id=event_id, task_id=task_id, assessment_status=assessment.status,
                assessment_source=assessment.source, assessment_reason=assessment.reason,
                assessment_error=assessment.error_code, goal_status=assessment.goal_status,
            )
            profile.record_interaction(record)
            scoped = profile
            if task_id is not None:
                key = (task_id, *self._key(initiator, target))
                original_scoped = self._task_profiles.get(key)
                scoped = copy.deepcopy(original_scoped) if original_scoped else RelationProfile(
                    agent_a=key[1], agent_b=key[2], max_history_length=self.max_history)
                prev_balance = scoped.balance if scoped.interaction_count else None
                scoped.record_interaction(copy.deepcopy(record))
            risk = self.risk_for(scoped)
            incident_id = self.recovery.observe(record, risk)
            self.profiles[pair_key] = profile
            if task_id is not None:
                self._task_profiles[key] = scoped
            self.event_records[event_id] = copy.deepcopy(record)
            self._previous_moloch_zones = self._zones
            self._zones = self._detect_moloch_zones(pair_key)
            result = {
                "balance": scoped.balance, "trust": scoped.trust,
                "relation_type": scoped.relation_type.value,
                "impact": ((assessment.impact_a + assessment.impact_b) / 2
                           if assessment.status == "assessed" else None),
                "interaction_count": scoped.interaction_count, "risk": risk,
                "prev_balance": prev_balance, "event_id": event_id, "task_id": task_id,
                "assessment_status": assessment.status, "assessment_source": assessment.source,
                "reason": assessment.reason, "error_code": assessment.error_code,
                "assessment_version": assessment.version, "model": assessment.model,
                "goal_status": assessment.goal_status, "confidence": scoped.confidence if risk != "unknown" else 0.0,
                "incident_id": incident_id, "duplicate": False,
            }
            self._events[event_id] = (fingerprint, copy.deepcopy(result))
            return result

    def can_cooperate(self, agent_a: str, agent_b: str, threshold: float = 0.7,
                      is_repair_attempt: bool = False, task_id: Optional[str] = None) -> Dict:
        """Advisory diagnosis only; never grants host execution authority."""
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not math.isfinite(threshold) or not 0 <= threshold <= 1:
            raise ValueError("threshold 必须为 0 到 1 的有限数值")
        with self.lock:
            profile = self.get_relationship(agent_a, agent_b, task_id)
            issues = self.recovery.open_for_pair(agent_a, agent_b, task_id)
            risk = self.risk_for(profile) if profile else "unknown"
            result = {"can": False, "decision": "unknown", "reason": "证据不足，按宿主原有规则处理",
                      "confidence": profile.confidence if profile else 0.0,
                      "risk": risk, "needs_repair": bool(issues), "task_id": task_id,
                      "incident_ids": [i["id"] for i in issues],
                      "advisory_only": True, "mode": "observe"}
            if is_repair_attempt:
                result.update(decision="repair_required", needs_repair=bool(issues),
                              reason="请为具体异常创建受限恢复方案；修复建议不会放行原任务")
            elif issues:
                result.update(decision="review", reason="存在未解决异常，请查看原任务依据及恢复建议")
            elif not profile or risk == "unknown" or profile.assessed_count < 3:
                pass
            elif profile.balance >= threshold:
                result.update(can=True, decision="continue",
                              reason=f"达到配置门槛；当前风险 {risk}，仍须遵守宿主权限与任务限制")
            else:
                result.update(decision="review", needs_repair=False,
                              reason="近期协作表现未达到配置门槛，建议核对任务依据；尚无需要恢复的具体异常")
            return result

    # ── Moloch 检测 ──

    def _detect_moloch_zones(self, changed_pair=None) -> List[Dict]:
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
            and profile.assessed_count >= 3
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
                relevant_change = changed_pair is None or set(changed_pair).issubset(component)
                if prev and not relevant_change:
                    trend = prev["trend"]
                elif prev:
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
                        "first_detected": (prev["first_detected"] if prev and not relevant_change else prev is None),
                    }
                )

        return moloch_zones

    def get_moloch_report(self) -> Dict:
        """获取 Moloch 检测报告"""
        zones = copy.deepcopy(self._zones)
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
