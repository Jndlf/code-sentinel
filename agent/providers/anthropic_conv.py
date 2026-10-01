"""Anthropic Messages API (auch über Langdocks Anthropic-Endpunkt)."""
from __future__ import annotations

from typing import Any

from ..config import ModelProfile
from .base import STUB, Conversation, Step, ToolCall


class AnthropicConversation(Conversation):
    def __init__(self, profile: ModelProfile, system: str, tools: list[dict[str, Any]],
                 max_tokens: int, client: Any = None):
        if client is None:
            import anthropic
            import os

            key = os.environ[profile.api_key_env]
            kw = {"auth_token": key} if profile.auth == "bearer" else {"api_key": key}
            client = anthropic.Anthropic(base_url=profile.base_url, max_retries=8, **kw)
        self.client = client
        self.base_url = str(profile.base_url)
        self.model = profile.model
        self.system = system
        self.tools = tools  # Anthropic-Format = unser Schema-Format
        self.max_tokens = max_tokens
        self.messages: list[dict] = []
        self._result_batches: list[list[dict]] = []

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def step(self, force_tool: str | None = None) -> Step:
        extra = {"tool_choice": {"type": "tool", "name": force_tool}} if force_tool else {}
        resp = self.client.messages.create(
            model=self.model, max_tokens=self.max_tokens, system=self.system,
            tools=self.tools, messages=self.messages, **extra,
        )
        self.messages.append({"role": "assistant", "content": resp.content})
        return Step(
            text="".join(b.text for b in resp.content if b.type == "text"),
            tool_calls=[ToolCall(b.id, b.name, dict(b.input)) for b in resp.content if b.type == "tool_use"],
            input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens,
            model=resp.model, stop_reason=str(resp.stop_reason),
        )

    def add_tool_results(self, results: list[tuple[str, str, bool]]) -> None:
        blocks = [{"type": "tool_result", "tool_use_id": i, "content": t, "is_error": e}
                  for i, t, e in results]
        self._result_batches.append(blocks)
        self.messages.append({"role": "user", "content": blocks})

    def trim_old_results(self, keep: int) -> None:
        old = self._result_batches[:-keep] if keep > 0 else self._result_batches
        for batch in old:
            for b in batch:
                b["content"] = STUB
