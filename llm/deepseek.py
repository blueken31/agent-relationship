"""
Agent Relationship — DeepSeekLLM

DeepSeek API 后端 — OpenAI 兼容接口。
默认 deepseek-v4-flash (无推理模式，适合 Agent 交互)。

环境变量:
  DEEPSEEK_API_KEY   — API 密钥 (必填)
  DEEPSEEK_BASE_URL  — API 端点 (默认 https://api.deepseek.com)
  DEEPSEEK_MODEL     — 模型名 (默认 deepseek-v4-flash)

可用模型:
  - deepseek-v4-flash: 高吞吐、无推理模式、成本低 (默认)
  - deepseek-v4-pro:  复杂推理、内置思维链 — 注意: 可能因
                       thinking 消耗 token 导致 content 为空
"""

import os
from typing import Optional
from .base import LLMBackend
from .openai import OpenAILLM


class DeepSeekLLM(OpenAILLM):
    """
    DeepSeek API — OpenAI 兼容但独立端点。

    与 OpenAI 的核心差异:
      - 端点: https://api.deepseek.com (无 /v1 后缀)
      - 上下文: 1M tokens
      - 定价: ~$0.28/$1.10 per 1M input/output
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: str = "deepseek-v4-flash",
    ):
        # 不走 OpenAILLM.__init__，避免 OPENAI_MODEL 环境变量覆盖
        LLMBackend.__init__(self)
        _api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        if not _api_key:
            raise ValueError(
                "DEEPSEEK_API_KEY 未设置。请设置环境变量或传入 api_key 参数。"
            )
        self.api_key = _api_key
        self.base_url = base_url or os.environ.get(
            "DEEPSEEK_BASE_URL", "https://api.deepseek.com"
        )
        self.model = os.environ.get("DEEPSEEK_MODEL", model)
