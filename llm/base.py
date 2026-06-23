"""
Agent Relationship — LLM 抽象基类

所有 LLM 后端必须实现的接口。
"""

import json
from abc import ABC, abstractmethod


class LLMBackend(ABC):
    """LLM 后端抽象基类"""

    @abstractmethod
    def chat(
        self,
        prompt: str,
        system: str = "",
        temperature: float = 0.3,
        max_tokens: int = 500,
        json_mode: bool = False,
    ) -> str:
        """通用对话接口"""
        ...

    # ── estimate_impact ──

    def estimate_impact(
        self,
        agent_id: str,
        interaction: dict,
        is_initiator: bool,
    ) -> float:
        """
        评估交互对 Agent 的影响 → (-1.0, +1.0)

        优先用 LLM 语义评估，失败时降级到启发式。
        """
        role = "发起方" if is_initiator else "接收方"
        prompt = (
            f"你是 {agent_id} ({role})。评估以下交互对你的影响:\n\n"
            f"交互详情: {json.dumps(interaction, ensure_ascii=False)}\n\n"
            f'返回 JSON:\n'
            f'{{"impact": <float -1到1>, '
            f'"reasoning": "<一句话分析>", '
            f'"fairness": <float 0-1>}}'
        )
        try:
            result = json.loads(self.chat(prompt, json_mode=True))
            return max(-1.0, min(1.0, float(result.get("impact", 0.0))))
        except Exception:
            return self._fallback_estimate_impact(interaction, is_initiator)

    @staticmethod
    def _fallback_estimate_impact(
        interaction: dict, is_initiator: bool
    ) -> float:
        """LLM 不可用时的启发式降级"""
        atype = interaction.get("action", interaction.get("type", ""))
        details = interaction.get("details", interaction.get("metadata", {}))

        beneficial = {
            "resource_exchange": 0.3,
            "collaboration": 0.4,
            "collaborate": 0.4,
            "knowledge_share": 0.35,
            "help": 0.5,
            "delegate_task": 0.3,
            "handoff": 0.2,
        }
        harmful = {
            "attack": -0.5,
            "deception": -0.4,
            "theft": -0.6,
            "sabotage": -0.7,
        }

        if atype in beneficial:
            return beneficial[atype]
        if atype in harmful:
            return harmful[atype]

        fair = details.get("fair_price", 0)
        actual = details.get("price", details.get("amount", 0))
        if fair > 0:
            return (
                0.3
                if (actual <= fair if is_initiator else actual >= fair)
                else -0.1
            )
        return 0.1
