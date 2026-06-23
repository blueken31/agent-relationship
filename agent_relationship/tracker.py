"""
Agent Relationship — RelationshipTracker v0.2.0

面向开发者的公开 API:
  track / health / can_cooperate / network / history
  detect_moloch / repair_paths / summary
"""

import io
import time
from typing import Callable, Dict, List, Optional, Tuple

from .engine import RelationshipEngine
from .llm import LLMBackend, MockLLM
from .repair import RepairMechanism
from .types import (
    Health, TrackResult, NetworkReport, MolochReport, MolochZone, RepairPath,
)


def _compute_risk(balance: float) -> str:
    if balance >= 0.7:
        return "low"
    elif balance >= 0.5:
        return "medium"
    elif balance >= 0.3:
        return "high"
    return "critical"


class RelationshipTracker:
    """
    关系跟踪器 — 给多 Agent 系统注入关系感知。

    v0.2.0 新增:
      - can_cooperate() — 集成到 Agent 决策循环
      - history() — 查询交互历史
      - TrackResult.risk + risk_transition — 无需单独调用 health()
      - on_risk_change 回调 — 风险跨越阈值时自动通知
      - MolochZone 类型化 — 可编程检测结果
    """

    def __init__(
        self,
        llm: str = "mock",
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        temperature: float = 0.3,
        moloch_threshold: float = 0.3,
        moloch_min_zone_size: int = 3,
        max_history: int = 100,
        on_risk_change: Optional[
            Callable[[str, str, str, str, float], None]
        ] = None,
    ):
        """
        Args:
            llm: "mock" | "openai" | "deepseek" | "anthropic"
            model: 覆盖默认模型
            api_key: API 密钥
            temperature: LLM 采样温度
            moloch_threshold: Moloch 检测阈值
            moloch_min_zone_size: 最小 Moloch 区域大小
            max_history: 每对关系最大交互记录数
            on_risk_change: 风险等级变化回调
                签名: (agent_a, agent_b, old_risk, new_risk, balance) -> None
        """
        self._backend = self._create_llm(llm, model, api_key)
        self._engine = RelationshipEngine(self._backend, max_history=max_history)
        self._engine.moloch_threshold = moloch_threshold
        self._engine.moloch_min_zone_size = moloch_min_zone_size
        self._temperature = temperature
        self._max_history = max_history
        self._on_risk_change = on_risk_change
        # 缓存上次 risk，用于检测过渡 (规范化 key)
        self._risk_cache: Dict[Tuple[str, str], str] = {}

    def _create_llm(
        self,
        llm_type: str,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> LLMBackend:
        """工厂方法"""
        if llm_type == "mock":
            return MockLLM()
        elif llm_type == "openai":
            from .llm.openai import OpenAILLM

            kwargs = {}
            if model:
                kwargs["model"] = model
            if api_key:
                kwargs["api_key"] = api_key
            return OpenAILLM(**kwargs)
        elif llm_type == "deepseek":
            from .llm.deepseek import DeepSeekLLM

            kwargs = {}
            if model:
                kwargs["model"] = model
            if api_key:
                kwargs["api_key"] = api_key
            return DeepSeekLLM(**kwargs)
        elif llm_type == "anthropic":
            from .llm.anthropic import AnthropicLLM

            kwargs = {}
            if model:
                kwargs["model"] = model
            if api_key:
                kwargs["api_key"] = api_key
            return AnthropicLLM(**kwargs)
        else:
            raise ValueError(
                f"不支持的 LLM 类型: '{llm_type}'。"
                f"可用: mock, openai, deepseek, anthropic"
            )

    def _norm_key(self, a: str, b: str) -> Tuple[str, str]:
        """规范化 Agent pair key — (a,b) 和 (b,a) 指向同一条记录"""
        return (a, b) if a < b else (b, a)

    # ═══════════════════════════════════════════════════════════════
    # core API
    # ═══════════════════════════════════════════════════════════════

    def track(
        self,
        agent_a: str,
        agent_b: str,
        interaction: Dict,
    ) -> TrackResult:
        """
        记录一次 Agent 交互。

        v0.2.0: 返回结果包含 risk + risk_transition，
        无需再单独调用 health() 来判断关系是否恶化。
        """
        # 记录交互前的 balance
        prev_profile = self._engine.get_relationship(agent_a, agent_b)
        prev_balance = prev_profile.balance if prev_profile else None

        result = self._engine.record_interaction(
            agent_a, agent_b, interaction
        )
        balance = result["balance"]
        risk = _compute_risk(balance)

        # 计算 risk 过渡 (使用规范化 key)
        pair_key = self._norm_key(agent_a, agent_b)
        prev_risk = self._risk_cache.get(pair_key)
        transition = None
        if prev_risk:
            risk_order = ["low", "medium", "high", "critical"]
            if risk_order.index(risk) < risk_order.index(prev_risk):
                transition = "improved"
            elif risk_order.index(risk) > risk_order.index(prev_risk):
                transition = "worsened"
            else:
                transition = "stable"

        self._risk_cache[pair_key] = risk

        # 触发回调
        if (
            self._on_risk_change
            and prev_risk
            and prev_risk != risk
        ):
            self._on_risk_change(
                agent_a, agent_b, prev_risk, risk, balance
            )

        return TrackResult(
            balance=balance,
            relation_type=result["relation_type"],
            impact=result["impact"],
            interaction_count=result["interaction_count"],
            risk=risk,
            prev_balance=prev_balance,
            risk_transition=transition,
        )

    def health(self, agent_a: str, agent_b: str) -> Optional[Health]:
        """查询双边关系健康度"""
        profile = self._engine.get_relationship(agent_a, agent_b)
        if not profile:
            return None

        return Health(
            balance=profile.balance,
            trust=profile.trust,
            relation_type=profile.relation_type.value,
            interaction_count=profile.interaction_count,
            symbiotic=profile.symbiotic_count,
            parasitic=profile.parasitic_count,
            conflictual=profile.conflictual_count,
            power_asymmetry=profile.power_asymmetry,
            risk=_compute_risk(profile.balance),
        )

    def can_cooperate(
        self,
        agent_a: str,
        agent_b: str,
        threshold: float = 0.4,
        try_repair: bool = False,
    ) -> Dict:
        """
        ★ v0.2.0 新增 — 集成到 Agent 决策循环。

        检查两个 Agent 是否可以协作。
        不是简单的是/否 — 关系恶化时返回修复路径。

        Args:
            agent_a, agent_b: Agent ID
            threshold: balance 门槛 (默认 0.4)
            try_repair: 如果关系不足，是否尝试修复

        Returns:
            {
                "can": bool,
                "reason": str,
                "needs_repair": bool,
                "repair_paths": [str, ...],   # 仅 needs_repair 时
                "confidence": float,
            }
        """
        return self._engine.can_cooperate(
            agent_a, agent_b,
            threshold=threshold,
            is_repair_attempt=try_repair,
        )

    def history(
        self,
        agent_a: str,
        agent_b: str,
        limit: int = 20,
    ) -> List[Dict]:
        """
        ★ v0.2.0 新增 — 查询交互历史。

        Returns:
            按时间倒序的交互记录列表:
            [{"action": ..., "impact_a": ..., "impact_b": ..., "timestamp": ...}, ...]
        """
        profile = self._engine.get_relationship(agent_a, agent_b)
        if not profile:
            return []

        records = sorted(
            profile.interaction_history,
            key=lambda r: r.timestamp,
            reverse=True,
        )[:limit]

        return [
            {
                "timestamp": r.timestamp,
                "action": r.interaction_type,
                "initiator": r.initiator,
                "target": r.target,
                "impact_a": r.impact_a,
                "impact_b": r.impact_b,
                "net_impact": r.net_impact,
                "is_symbiotic": r.is_symbiotic,
                "is_parasitic": r.is_parasitic,
                "is_conflictual": r.is_conflictual,
            }
            for r in records
        ]

    def network(
        self, agent_ids: Optional[List[str]] = None
    ) -> NetworkReport:
        """全网关系矩阵"""
        if agent_ids is None:
            agent_ids = self._engine.all_agent_ids()

        pairs: Dict = {}
        balances = []
        weakest = None

        for i, a in enumerate(agent_ids):
            for b in agent_ids[i + 1 :]:
                key = (a, b)
                profile = self._engine.get_relationship(a, b)
                if profile:
                    val = profile.balance
                    pairs[key] = val
                    balances.append(val)
                    if weakest is None or val < weakest[2]:
                        weakest = (a, b, val)
                else:
                    pairs[key] = None

        avg = sum(balances) / len(balances) if balances else 0.5

        return NetworkReport(
            pairs=pairs,
            avg_balance=avg,
            weakest_link=weakest,
            agent_count=len(agent_ids),
            relationship_count=len(balances),
        )

    def detect_moloch(self) -> MolochReport:
        """
        ★ v0.2.0: 返回类型化的 MolochZone 列表。

        扫描全网，检测 Moloch 竞争升级区域。
        """
        report = self._engine.get_moloch_report()

        # 转换为 MolochZone 类型
        typed_zones = [
            MolochZone(
                agents=z["agents"],
                size=z["size"],
                avg_balance=z["avg_balance"],
                trend=z.get("trend", "stable"),
                severity=z.get("severity", "watch"),
                first_detected=z.get("first_detected", True),
            )
            for z in report["zones"]
        ]

        # at_risk_pairs
        at_risk = []
        for (a, b), profile in self._engine.profiles.items():
            if (
                profile.balance < self._engine.moloch_threshold
                and profile.interaction_count >= 3
            ):
                in_zone = any(
                    a in z.agents and b in z.agents
                    for z in typed_zones
                )
                if not in_zone:
                    at_risk.append((a, b, profile.balance))

        return MolochReport(
            active=report["active"],
            zones=typed_zones,
            total_relationships=report["total_relationships"],
            balanced_ratio=report["balanced_ratio"],
            at_risk_pairs=sorted(at_risk, key=lambda x: x[2]),
        )

    def repair_paths(
        self, agent_a: str, agent_b: str
    ) -> List[RepairPath]:
        """
        返回可用的关系修复路径。

        根据 balance 过滤：balance 极低时排除低风险路径，
        优先推荐高成功率路径。
        """
        all_paths = RepairMechanism.available_paths()
        h = self.health(agent_a, agent_b)

        if h and h.balance < 0.15:
            # 极度恶化：排除低成功率路径
            all_paths = [
                p for p in all_paths
                if p["success_probability"] >= 0.6
            ]

        return [
            RepairPath(
                path=p["path"],
                description=p["description"],
                cost=p["cost"],
                success_probability=p["success_probability"],
            )
            for p in all_paths
        ]

    def summary(self) -> str:
        """一键生成 Markdown 格式的系统健康摘要"""
        net = self.network()
        moloch = self.detect_moloch()

        buf = io.StringIO()
        buf.write("# Agent 关系健康摘要\n\n")
        buf.write("## 概览\n")
        buf.write(f"- Agent 总数: {net.agent_count}\n")
        buf.write(f"- 关系总数: {net.relationship_count}\n")
        buf.write(f"- 平均 balance: {net.avg_balance:.3f}\n")
        buf.write(
            f"- Moloch 检测: "
            f"{'🚨 激活' if moloch.active else '未激活'}\n"
        )

        if moloch.zones:
            buf.write("\n### Moloch 区域\n")
            for z in moloch.zones:
                buf.write(
                    f"- {', '.join(z.agents)} "
                    f"(balance={z.avg_balance:.2f}, "
                    f"严重性={z.severity}, "
                    f"趋势={z.trend}, "
                    f"{'首次检出' if z.first_detected else '持续'})\n"
                )

        buf.write("\n## 关系详情\n")
        buf.write(
            "| Agent A | Agent B | balance | 类型 | 风险 |\n"
        )
        buf.write(
            "|---------|---------|:-------:|------|:----:|\n"
        )

        sorted_pairs = sorted(
            net.pairs.items(),
            key=lambda x: x[1] if x[1] is not None else -1,
        )
        for (a, b), bal in sorted_pairs:
            h = self.health(a, b)
            if h:
                buf.write(
                    f"| {a} | {b} | {bal:.2f} | {h.relation_type} "
                    f"| {h.risk} |\n"
                )
            else:
                buf.write(f"| {a} | {b} | — | — | — |\n")

        if moloch.at_risk_pairs:
            buf.write("\n## 风险警告\n")
            for a, b, bal in moloch.at_risk_pairs:
                risk = "critical" if bal < 0.3 else "high"
                buf.write(
                    f"⚠️ {a} ↔ {b}: balance={bal:.2f} "
                    f"({risk}) — 建议修复\n"
                )

        return buf.getvalue()

    # ═══════════════════════════════════════════════════════════════
    # 底层访问
    # ═══════════════════════════════════════════════════════════════

    @property
    def engine(self) -> RelationshipEngine:
        """获取底层引擎 (高级用途)"""
        return self._engine

    @property
    def llm_type(self) -> str:
        """获取当前 LLM 后端类型名"""
        return type(self._backend).__name__
