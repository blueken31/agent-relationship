"""
Agent Relationship — RelationshipTracker v0.4.0

面向开发者的公开 API:
  track / health / can_cooperate / network / history
  detect_moloch / repair_paths / summary
"""

import io
import copy
import math
from contextlib import contextmanager
from functools import wraps
from os import PathLike
from typing import Callable, Dict, List, Optional, Tuple, Union

from .engine import RelationshipEngine
from .llm import LLMBackend, MockLLM
from .repair import RepairMechanism
from .types import (
    Health, TrackResult, NetworkReport, MolochReport, MolochZone, RepairPath,
)


def _snapshot_read(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._state_access():
            return method(self, *args, **kwargs)
    return wrapped


def _snapshot_write(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._state_access(write=True):
            return method(self, *args, **kwargs)
    return wrapped


class RelationshipTracker:
    """
    关系跟踪器 — 记录任务协作依据与受限恢复结果。

    v0.2.0 新增:
      - can_cooperate() — 集成到 Agent 决策循环
      - history() — 查询交互历史
      - TrackResult.risk + risk_transition — 无需单独调用 health()
      - on_risk_change 回调 — 风险跨越阈值时自动通知
      - MolochZone 类型化 — 可编程检测结果
    """

    def __init__(
        self,
        llm: Union[str, LLMBackend] = "mock",
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        temperature: float = 0.3,
        moloch_threshold: float = 0.3,
        moloch_min_zone_size: int = 3,
        max_history: int = 100,
        on_risk_change: Optional[
            Callable[[str, str, str, str, float], None]
        ] = None,
        storage_path: Optional[Union[str, PathLike]] = None,
        storage_timeout: float = 5.0,
    ):
        """
        Args:
            llm: "mock" | "openai" | "deepseek" | "anthropic" 或 LLMBackend 实例
            model: 覆盖默认模型
            api_key: API 密钥
            temperature: LLM 采样温度
            moloch_threshold: Moloch 检测阈值
            moloch_min_zone_size: 最小 Moloch 区域大小
            max_history: 每对关系最大交互记录数
            storage_path: 可选 SQLite 文件；省略时保留内存模式
            storage_timeout: 等待其他 SQLite 写事务的最长秒数
            on_risk_change: 风险等级变化回调
                签名: (agent_a, agent_b, old_risk, new_risk, balance) -> None
        """
        self._backend = self._create_llm(llm, model, api_key)
        if isinstance(moloch_threshold, bool) or not isinstance(moloch_threshold, (int, float)) or not math.isfinite(moloch_threshold) or not 0 <= moloch_threshold <= 1:
            raise ValueError("moloch_threshold 必须为 0 到 1 的有限数值")
        if isinstance(moloch_min_zone_size, bool) or not isinstance(moloch_min_zone_size, int) or moloch_min_zone_size < 2:
            raise ValueError("moloch_min_zone_size 必须为至少 2 的整数")
        self._engine = RelationshipEngine(self._backend, max_history=max_history, temperature=temperature)
        self._track_results = {}
        self._engine.moloch_threshold = moloch_threshold
        self._engine.moloch_min_zone_size = moloch_min_zone_size
        self._temperature = temperature
        self._max_history = max_history
        self._on_risk_change = on_risk_change
        # 缓存上次 risk，用于检测过渡 (规范化 key)
        self._risk_cache: Dict[Tuple[Optional[str], str, str], str] = {}
        self._storage = None
        self._state_depth = 0
        self._state_writing = False
        self._closed = False
        if storage_path is not None:
            from .storage import SQLiteStore
            from .state import export_state, restore_state

            self._storage = SQLiteStore(storage_path, timeout=storage_timeout)
            try:
                # Initialization is also serialized so two first openers cannot
                # overwrite each other's initial checkpoint.
                with self._storage.transaction(write=True) as transaction:
                    saved = transaction.load()
                    if saved is None:
                        transaction.save(export_state(self))
                    else:
                        restore_state(self, saved)
            except BaseException:
                self._storage.close()
                raise

    @contextmanager
    def _state_access(self, write: bool = False):
        """Refresh under a transaction; commit before publishing any callback.

        One tracker lock protects nested public calls. SQLite serializes separate
        instances/processes; no stale in-memory state is written over newer data.
        """
        with self._engine.lock:
            if self._closed:
                raise RuntimeError("tracker is closed")
            if self._state_depth:
                if write and not self._state_writing:
                    raise RuntimeError("cannot mutate during a read snapshot")
                if write and self._storage is not None:
                    raise RuntimeError("cannot reenter a persistent mutation")
                yield
                return
            self._state_depth = 1
            self._state_writing = write
            baseline = None
            try:
                if self._storage is None:
                    yield
                else:
                    from .state import export_state, restore_state
                    from .storage import StorageError

                    try:
                        with self._storage.transaction(write=write) as transaction:
                            saved = transaction.load()
                            if saved is None:
                                raise StorageError("persistent checkpoint is missing")
                            restore_state(self, saved)
                            baseline = saved
                            yield
                            if write:
                                current = export_state(self)
                                if current != baseline:
                                    transaction.save(current)
                    except BaseException:
                        if baseline is not None:
                            # A failed save or commit must not leave the in-memory
                            # instance claiming that an uncommitted action happened.
                            restore_state(self, baseline)
                        raise
            finally:
                self._state_depth = 0
                self._state_writing = False

    def close(self) -> None:
        """Release the optional database. This never executes or resumes a task."""
        with self._engine.lock:
            if self._state_depth:
                raise RuntimeError("cannot close tracker during an active operation")
            if not self._closed:
                if self._storage is not None:
                    self._storage.close()
                self._closed = True

    def __enter__(self):
        if self._closed:
            raise RuntimeError("tracker is closed")
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()

    def _create_llm(
        self,
        llm_type: Union[str, LLMBackend],
        model: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> LLMBackend:
        """工厂方法"""
        if isinstance(llm_type, LLMBackend):
            return llm_type
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
        with self._state_access(write=True):
            result = self._engine.record_interaction(agent_a, agent_b, interaction)
            event_id = result["event_id"]
            if result["duplicate"] and event_id in self._track_results:
                previous = copy.deepcopy(self._track_results[event_id])
                previous.duplicate = True
                return previous
            pair_key = (result["task_id"], *self._norm_key(agent_a, agent_b))
            prev_risk = self._risk_cache.get(pair_key, "unknown")
            risk = result["risk"]
            transition = None
            if prev_risk == "unknown" and risk != "unknown":
                transition = "first_observation"
            elif risk == "unknown":
                transition = "unknown"
            else:
                order = ["low", "medium", "high", "critical"]
                transition = ("improved" if order.index(risk) < order.index(prev_risk)
                              else "worsened" if order.index(risk) > order.index(prev_risk) else "stable")
            self._risk_cache[pair_key] = risk
            output = TrackResult(
                balance=result["balance"], relation_type=result["relation_type"],
                impact=result["impact"], interaction_count=result["interaction_count"],
                risk=risk, prev_balance=result["prev_balance"], risk_transition=transition,
                event_id=event_id, task_id=result["task_id"],
                assessment_status=result["assessment_status"], assessment_source=result["assessment_source"],
                reason=result["reason"], error_code=result["error_code"],
                assessment_version=result["assessment_version"], model=result["model"],
                goal_status=result["goal_status"], confidence=result["confidence"],
                incident_id=result["incident_id"], duplicate=result["duplicate"],
            )
            # Commit before notifying, so a callback retry cannot record or notify twice.
            self._track_results[event_id] = copy.deepcopy(output)
            notify = not result["duplicate"] and self._on_risk_change and prev_risk != risk and (
                prev_risk != "unknown" or risk in ("high", "critical"))
        if notify:
            try:
                self._on_risk_change(agent_a, agent_b, prev_risk, risk, output.balance)
            except Exception:
                output.notification_error = "callback_failed"
                try:
                    with self._state_access(write=True):
                        self._track_results[event_id] = copy.deepcopy(output)
                except Exception:
                    # Recording already committed. Failure to persist a callback
                    # diagnostic must not masquerade as an event-write failure.
                    output.notification_error = "callback_failed_status_unpersisted"
        return output

    @_snapshot_read
    def health(self, agent_a: str, agent_b: str, task_id: Optional[str] = None) -> Optional[Health]:
        """查询双边关系健康度"""
        profile = self._engine.get_relationship(agent_a, agent_b, task_id)
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
            risk=self._engine.risk_for(profile),
            confidence=profile.confidence if self._engine.risk_for(profile) != "unknown" else 0.0,
            assessment_status=(profile.interaction_history[-1].assessment_status
                               if profile.interaction_history else "unknown"),
            open_incident_count=len(self._engine.recovery.open_for_pair(agent_a, agent_b, task_id)),
        )

    @_snapshot_read
    def can_cooperate(
        self,
        agent_a: str,
        agent_b: str,
        threshold: float = 0.7,
        try_repair: bool = False,
        task_id: Optional[str] = None,
    ) -> Dict:
        """
        ★ v0.2.0 新增 — 集成到 Agent 决策循环。

        检查两个 Agent 是否可以协作。
        不是简单的是/否 — 关系恶化时返回修复路径。

        Args:
            agent_a, agent_b: Agent ID
            threshold: balance 门槛 (默认 0.7)
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
            is_repair_attempt=try_repair, task_id=task_id,
        )

    @_snapshot_read
    def history(
        self,
        agent_a: str,
        agent_b: str,
        limit: int = 20,
        task_id: Optional[str] = None,
    ) -> List[Dict]:
        """
        ★ v0.2.0 新增 — 查询交互历史。

        Returns:
            按时间倒序的交互记录列表:
            [{"action": ..., "impact_a": ..., "impact_b": ..., "timestamp": ...}, ...]
        """
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError("limit 必须为非负整数")
        profile = self._engine.get_relationship(agent_a, agent_b, task_id)
        if not profile:
            return []

        records = sorted(
            profile.interaction_history,
            key=lambda r: r.timestamp,
            reverse=True,
        )[:limit]

        return [self._event_snapshot(r) for r in records]

    def _event_snapshot(self, r) -> Dict:
        metadata = self._engine._events.get(r.event_id, (None, {}))[1]
        return {
            "event_id": r.event_id, "task_id": r.task_id, "timestamp": r.timestamp,
            "action": r.interaction_type, "initiator": r.initiator, "target": r.target,
            "context": copy.deepcopy(r.context), "result": copy.deepcopy(r.context.get("result")),
            "narrative": copy.deepcopy(r.context.get("narrative")), "impact_a": r.impact_a, "impact_b": r.impact_b,
            "net_impact": r.net_impact, "is_symbiotic": r.is_symbiotic,
            "is_parasitic": r.is_parasitic, "is_conflictual": r.is_conflictual,
            "assessment_status": r.assessment_status, "assessment_source": r.assessment_source,
            "reason": r.assessment_reason, "error_code": r.assessment_error,
            "goal_status": r.goal_status, "assessment_version": metadata.get("assessment_version"),
            "model": metadata.get("model"),
        }

    @_snapshot_read
    def event(self, event_id: str) -> Optional[Dict]:
        """Retrieve retained evidence even after the rolling history window expires."""
        record = self._engine.event_records.get(event_id)
        return self._event_snapshot(record) if record else None

    @_snapshot_read
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
                if profile and self._engine.risk_for(profile) != "unknown":
                    val = profile.balance
                    pairs[key] = val
                    balances.append(val)
                    if weakest is None or val < weakest[2]:
                        weakest = (a, b, val)
                else:
                    pairs[key] = None

        avg = sum(balances) / len(balances) if balances else None

        return NetworkReport(
            pairs=pairs,
            avg_balance=avg,
            weakest_link=weakest,
            agent_count=len(agent_ids),
            relationship_count=sum(self._engine.get_relationship(a, b) is not None for a, b in pairs),
        )

    @_snapshot_read
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
                and profile.assessed_count >= 3
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

    @_snapshot_read
    def repair_paths(
        self, agent_a: str, agent_b: str
    ) -> List[RepairPath]:
        """返回描述性的恢复路径；成功率尚未校准，不构成执行授权。"""
        return [RepairPath(**path) for path in RepairMechanism.available_paths()]

    @_snapshot_read
    def incidents(self, task_id: Optional[str] = None, status: Optional[str] = None) -> List[Dict]:
        """Task-linked diagnostic cards, including evidence and current recovery state."""
        return self._engine.recovery.list_incidents(task_id, status)

    @_snapshot_write
    def link_incident(self, incident_id: str, task_id: str, reason: str) -> Dict:
        return self._engine.recovery.link_incident(incident_id, task_id, reason)

    @_snapshot_write
    def propose_repair(self, incident_id: str, action: str = "verify_handoff") -> Dict:
        return self._engine.recovery.propose_repair(incident_id, action)

    @_snapshot_write
    def begin_repair(self, plan_id: str, host_authorized: bool = False) -> Dict:
        """Record the host's authorization; this library does not execute any action."""
        return self._engine.recovery.begin_repair(plan_id, host_authorized)

    @_snapshot_write
    def cancel_repair(self, plan_id: str) -> Dict:
        return self._engine.recovery.cancel_repair(plan_id)

    @_snapshot_write
    def complete_repair(self, plan_id: str, outcome: str, evidence_event_id: str) -> Dict:
        with self._engine.lock:
            if evidence_event_id not in self._engine.event_records:
                raise ValueError("恢复结果必须引用已记录的验证事件")
            evidence = self._engine.event_records[evidence_event_id]
            return self._engine.recovery.complete_repair(plan_id, outcome, evidence)

    @_snapshot_read
    def summary(self) -> str:
        """一键生成 Markdown 格式的系统健康摘要"""
        net = self.network()
        moloch = self.detect_moloch()

        buf = io.StringIO()
        buf.write("# Agent 关系健康摘要\n\n")
        issues = self.incidents(status="open")
        buf.write("观察模式：以下为诊断建议，执行权限由宿主系统控制。\n\n")
        buf.write(f"待处理异常: {len(issues)}\n\n")
        for issue in issues:
            buf.write(f"- 任务 {issue.get('task_id') or '未关联'}：{issue.get('title', '协作异常')}；"
                      f"依据 {', '.join(issue.get('evidence_ids', []))}\n")
        buf.write("\n## 概览\n")
        buf.write(f"- Agent 总数: {net.agent_count}\n")
        buf.write(f"- 关系总数: {net.relationship_count}\n")
        average = f"{net.avg_balance:.3f}" if net.avg_balance is not None else "证据不足"
        buf.write(f"- 已评估关系平均 balance: {average}\n")
        buf.write(f"- 评估后端: {self.llm_type}（Mock 为模拟数据）\n")
        buf.write(
            f"- 协作异常区域检测: "
            f"{'🚨 激活' if moloch.active else '未激活'}\n"
        )

        if moloch.zones:
            buf.write("\n### 协作异常区域\n")
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
                value = format(bal, ".2f") if bal is not None else "证据不足"
                buf.write(
                    f"| {a} | {b} | {value} | {h.relation_type} "
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
        if self._storage is not None:
            raise RuntimeError("durable mode requires public tracker APIs; direct engine mutations are not persisted")
        return self._engine

    @property
    def llm_type(self) -> str:
        """获取当前 LLM 后端类型名"""
        return type(self._backend).__name__
