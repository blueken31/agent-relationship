"""
Agent Relationship — OpenAILLM

OpenAI Chat Completions API 后端。
支持 GPT-4o, GPT-4.1, Codex 等所有 OpenAI 模型。
"""

import json
import os
import urllib.request
import urllib.error
from typing import Optional
from .base import LLMBackend


class OpenAILLM(LLMBackend):
    """
    OpenAI API 后端。

    环境变量:
      OPENAI_API_KEY    — API 密钥 (必填)
      OPENAI_BASE_URL   — API 端点 (默认 https://api.openai.com/v1)
      OPENAI_MODEL      — 模型名 (默认 gpt-4o)
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: str = "gpt-4o",
    ):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.base_url = base_url or os.environ.get(
            "OPENAI_BASE_URL", "https://api.openai.com/v1"
        )
        self.model = os.environ.get("OPENAI_MODEL", model)

        if not self.api_key:
            raise ValueError(
                "OPENAI_API_KEY 未设置。请设置环境变量或传入 api_key 参数。"
            )

    def chat(
        self,
        prompt: str,
        system: str = "",
        temperature: float = 0.3,
        max_tokens: int = 500,
        json_mode: bool = False,
    ) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        body: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        endpoint = f"{self.base_url}/chat/completions"
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read())
                return data["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            raise RuntimeError(
                f"OpenAI API 错误 ({e.code}): {e.read().decode()}"
            )
        except Exception as e:
            raise RuntimeError(f"LLM 调用失败: {e}")
