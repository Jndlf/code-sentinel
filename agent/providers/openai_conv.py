"""OpenAI-kompatible Chat-Completions-API (z. B. Langdocks OpenAI-Endpunkt).

Übersetzt unsere Tool-Schemas (Anthropic-Format) in das OpenAI-Function-Format und Tool-Ergebnisse
in Nachrichten mit role="tool".
"""
from __future__ import annotations

import json
from typing import Any

from ..config import ModelProfile
from .base import STUB, Conversation, Step, ToolCall


def to_openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"type": "function",
         "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}}
        for t in tools
    ]


class OpenAIConversation(Conversation):
    def __init__(self, profile: ModelProfile, system: str, tools: list[dict[str, Any]],
                 max_tokens: int, client: Any = None):
        if client is None:
            import os
            from openai import OpenAI  # nur nötig, wenn ein OpenAI-Profil genutzt wird

            client = OpenAI(base_url=profile.base_url, api_key=os.environ[profile.api_key_env], max_retries=8)
        self.client = client
        self.base_url = str(profile.base_url)
        self.model = profile.model
        self.tools = to_openai_tools(tools)
        self.max_tokens = max_tokens
        self.max_tokens_param = profile.max_tokens_param
        self.reasoning_effort = profile.reasoning_effort
        self.messages: list[dict] = [{"role": "system", "content": system}]
        self._result_batches: list[list[dict]] = []

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def step(self, force_tool: str | None = None) -> Step:
        extra = {"reasoning_effort": self.reasoning_effort} if self.reasoning_effort else {}
        if force_tool:
            extra["tool_choice"] = {"type": "function", "function": {"name": force_tool}}
        resp = self.client.chat.completions.create(
            model=self.model, messages=self.messages, tools=self.tools,
            **{self.max_tokens_param: self.max_tokens}, **extra,
        )
        choice = resp.choices[0]
        msg = choice.message
        raw_calls = msg.tool_calls or []
        assistant: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
        if raw_calls:
            assistant["tool_calls"] = [
                {"id": c.id, "type": "function",
                 "function": {"name": c.function.name, "arguments": c.function.arguments}}
                for c in raw_calls
            ]
        self.messages.append(assistant)

        calls = []
        for c in raw_calls:
            try:
                args = json.loads(c.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append(ToolCall(c.id, c.function.name, args))
        u = resp.usage
        return Step(
            text=msg.content or "", tool_calls=calls,
            input_tokens=getattr(u, "prompt_tokens", 0) or 0,
            output_tokens=getattr(u, "completion_tokens", 0) or 0,
            model=resp.model, stop_reason=str(choice.finish_reason),
        )

    def add_tool_results(self, results: list[tuple[str, str, bool]]) -> None:
        batch = [{"role": "tool", "tool_call_id": i, "content": ("FEHLER: " if e else "") + t}
                 for i, t, e in results]
        self._result_batches.append(batch)
        self.messages.extend(batch)

    def trim_old_results(self, keep: int) -> None:
        old = self._result_batches[:-keep] if keep > 0 else self._result_batches
        for batch in old:
            for m in batch:
                m["content"] = STUB
