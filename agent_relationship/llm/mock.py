"""
Agent Relationship — MockLLM

确定性模拟 — 零依赖，立即可用。基于关键词匹配的上下文感知影响评估。
"""

import json
from .base import LLMBackend


class MockLLM(LLMBackend):
    """确定性 Mock — 零 API 调用，零成本"""

    def chat(
        self,
        prompt: str,
        system: str = "",
        temperature: float = 0.3,
        max_tokens: int = 500,
        json_mode: bool = False,
    ) -> str:
        prompt_lower = prompt.lower()

        # ── estimate_impact ──
        if "评估以下交互对你的影响" in prompt:
            impact = 0.35
            details = prompt_lower
            advantageous = any(
                w in details
                for w in ["帮助", "分享", "提升", "优化", "成功"]
            )
            harmful = any(
                w in details
                for w in [
                    "攻击", "欺骗", "窃取", "破坏", "失败",
                    "冲突", "conflict", "对立", "对抗",
                ]
            )
            unbalanced = any(
                w in details
                for w in ["独占", "垄断", "囤积", "搭便车", "超额"]
            )

            if harmful:
                impact = -0.6
            elif unbalanced:
                impact = -0.15
            elif advantageous:
                impact = 0.6

            return json.dumps(
                {
                    "impact": impact,
                    "reasoning": f"基于交互关键词的确定性评估",
                    "fairness": 0.7 if advantageous else 0.4,
                }
            )

        # ── 通用 JSON 响应 ──
        if json_mode:
            return json.dumps({"result": "ok", "confidence": 0.85})

        return "MockLLM 响应"
