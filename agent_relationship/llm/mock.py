"""Explicit simulated fixtures; this backend does not understand narratives."""

import json

from ..assessment import Assessment, finite_number
from .base import LLMBackend


class MockLLM(LLMBackend):
    """Deterministic demo data, zero API calls and no semantic accuracy claim."""

    _SCENARIOS = {
        "justified_review": (0.3, 0.3, "met", "正确审核维护共同目标"),
        "goal_violation": (0.6, 0.6, "violated", "双方受益但违反共同目标"),
        "asymmetric_harm": (0.9, -0.9, "violated", "一方获益、另一方受损"),
        "success": (0.6, 0.6, "met", "任务按约定完成"),
        "failure": (-0.6, -0.6, "violated", "交接未达到预期结果"),
    }
    _BENEFICIAL = {
        "help", "collaboration", "collaborate", "knowledge_share",
        "handoff", "delegate_task", "resource_exchange",
    }
    _HARMFUL = {"attack", "deception", "theft", "sabotage"}
    _SUCCESS = {"success", "completed", "ok", "成功"}
    _FAILURE = {"failed", "failure", "error", "失败"}

    def assess_interaction(self, initiator, target, interaction, temperature=0.3) -> Assessment:
        if not finite_number(temperature, 0.0, 2.0):
            raise ValueError("temperature must be a finite number in [0, 2]")
        if not isinstance(interaction, dict):
            return Assessment(
                status="unknown", source="mock", impact_a=None, impact_b=None,
                reason="模拟模式：事件必须是结构化对象。", error_code="invalid_input",
                model="mock-fixtures",
            )
        scenario = interaction.get("scenario")
        if scenario is not None:
            fixture = self._SCENARIOS.get(scenario) if isinstance(scenario, str) else None
        else:
            action = interaction.get("action", interaction.get("type"))
            result = interaction.get("result")
            fixture = None
            if isinstance(action, str) and isinstance(result, str):
                if action in self._HARMFUL or (action == "conflict" and result in self._FAILURE):
                    fixture = self._SCENARIOS["failure"]
                elif action in self._BENEFICIAL:
                    if result in self._SUCCESS:
                        fixture = self._SCENARIOS["success"]
                    elif result in self._FAILURE:
                        fixture = self._SCENARIOS["failure"]
        if fixture is None:
            return Assessment(
                status="unknown", source="mock", impact_a=None, impact_b=None,
                reason="模拟模式：未匹配明确测试场景，不评估叙述语义。",
                error_code="unsupported_mock_scenario", model="mock-fixtures",
            )
        a, b, goal, reason = fixture
        return Assessment(
            status="assessed", source="mock", impact_a=a, impact_b=b,
            reason=f"模拟场景：{reason}；未经真实模型或业务证据验证。",
            goal_status=goal, model="mock-fixtures",
        )

    def chat(self, prompt, system="", temperature=0.3, max_tokens=500, json_mode=False) -> str:
        """Generic demo reply; assessments use structured fixtures above."""
        if json_mode:
            return json.dumps({"result": "mock", "status": "unknown"})
        return "MockLLM 模拟响应"
