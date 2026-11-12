"""
Agent Relationship — AnthropicLLM

Anthropic Claude API 后端 — Messages API 格式。

环境变量:
  ANTHROPIC_API_KEY  — API 密钥 (必填)
  ANTHROPIC_MODEL     — 模型名 (默认 claude-sonnet-4-20250514)

与 OpenAI 格式的核心差异:
  - 端点: https://api.anthropic.com/v1/messages
  - 认证: x-api-key 头 (非 Authorization: Bearer)
  - 版本: anthropic-version: 2023-06-01 (必填)
  - system: 顶层字段 (非 messages 数组内)
  - 响应: content 数组内的 text 块
  - max_tokens: 必填 (OpenAI 可选)
  - JSON mode: 无原生支持，用 prompt 引导
"""

import json
import os
import urllib.request
import urllib.error
from typing import Optional
from .base import LLMBackend


class AnthropicLLM(LLMBackend):
    """
    Anthropic Claude API 后端 — Messages API。

    使用 urllib 直调 HTTP，无需 anthropic SDK。
    """

    ANTHROPIC_VERSION = "2023-06-01"
    ENDPOINT = "https://api.anthropic.com/v1/messages"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = model if model is not None else os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("model must be a nonempty string")

        if not self.api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY 未设置。"
                "请设置环境变量或传入 api_key 参数。"
            )

    def chat(
        self,
        prompt: str,
        system: str = "",
        temperature: float = 0.3,
        max_tokens: int = 500,
        json_mode: bool = False,
    ) -> str:
        # 构建请求体
        body: dict = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }

        # Anthropic: system 是顶层字段
        system_content = system or ""
        if json_mode:
            # Anthropic 无原生 JSON mode，通过 system prompt 引导
            json_instruction = (
                "\n\n你必须返回纯 JSON，只输出 JSON 对象，"
                "不要包含 markdown 代码块标记 (```json```)，"
                "不要添加任何解释文字。"
            )
            system_content += json_instruction

        if system_content.strip():
            body["system"] = system_content.strip()

        if temperature is not None:
            body["temperature"] = temperature

        req = urllib.request.Request(
            self.ENDPOINT,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": self.ANTHROPIC_VERSION,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read())
                content = data["content"]
                if not isinstance(content, list):
                    raise ValueError("missing content blocks")
                text = "".join(
                    block["text"] for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                    and isinstance(block.get("text"), str)
                )
                if not text.strip():
                    raise ValueError("missing text response")
                return text
        except urllib.error.HTTPError as e:
            raise RuntimeError(
                f"Anthropic API 请求失败 (HTTP {e.code})"
            ) from None
        except Exception:
            raise RuntimeError("Anthropic 调用失败或响应格式无效") from None
