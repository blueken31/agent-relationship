"""
Agent Relationship — OpenAILLM

OpenAI Chat Completions API 后端。
只适用于支持 Chat Completions 请求格式的模型；不提供 Responses API。
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
        model: Optional[str] = None,
    ):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.base_url = base_url or os.environ.get(
            "OPENAI_BASE_URL", "https://api.openai.com/v1"
        )
        self.model = model if model is not None else os.environ.get("OPENAI_MODEL", "gpt-4o")

        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("model must be a nonempty string")

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

        endpoint = f"{self.base_url.rstrip('/')}/chat/completions"
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
                content = data["choices"][0]["message"]["content"]
                if not isinstance(content, str) or not content.strip():
                    raise ValueError("missing text response")
                return content
        except urllib.error.HTTPError as e:
            raise RuntimeError(
                f"Chat Completions API 请求失败 (HTTP {e.code})"
            ) from None
        except Exception:
            raise RuntimeError("Chat Completions 调用失败或响应格式无效") from None
