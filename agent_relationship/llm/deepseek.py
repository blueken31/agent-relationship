"""
Agent Relationship — DeepSeekLLM

DeepSeek API 后端 — OpenAI 兼容接口。
仅使用 Chat Completions 请求格式，可显式配置服务端支持的模型。

环境变量:
  DEEPSEEK_API_KEY   — API 密钥 (必填)
  DEEPSEEK_BASE_URL  — API 端点 (默认 https://api.deepseek.com)
  DEEPSEEK_MODEL     — 模型名 (默认 deepseek-v4-flash)

"""

import os
from typing import Optional
from .base import LLMBackend
from .openai import OpenAILLM


class DeepSeekLLM(OpenAILLM):
    """
    DeepSeek API — OpenAI 兼容但独立端点。

    保持独立的凭据、端点与模型配置，不读取 OPENAI_MODEL。
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
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
        self.model = model if model is not None else os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("model must be a nonempty string")
