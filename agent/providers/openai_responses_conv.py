"""OpenAI Responses-API (`/v1/responses`), z. B. über Langdocks OpenAI-Endpunkt.

Nötig für Reasoning-Modelle mit Tools: `/chat/completions` lehnt Function-Tools zusammen mit Reasoning ab.

Der Verlauf wird clientseitig geführt (Liste von "input items"): Die Ausgabe-Items des Modells
(inkl. Reasoning-Items) werden unverändert zurückgegeben, sonst lehnt die API Tool-Aufrufe ab.
Nur die Tool-Ergebnisse älterer Runden werden zur Kostenersparnis gekürzt.
"""
from __future__ import annotations

from typing import Any

from ..config import ModelProfile
from .base import STUB, Conversation, Step, ToolCall


def to_responses_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Responses-Format: flach, ohne 'function'-Umschlag."""
    return [
        {"type": "function", "name": t["name"], "description": t["description"],
         "parameters": t["input_schema"]}
        for t in tools
    ]


def _to_dict(item: Any) -> dict[str, Any]:
    if hasattr(item, "model_dump"):
        return item.model_dump(exclude_none=True)
    return {k: v for k, v in vars(item).items() if v is not None}


class OpenAIResponsesConversation(Conversation):
    def __init__(self, profile: ModelProfile, system: str, tools: list[dict[str, Any]],
                 max_tokens: int, client: Any = None):
        if client is None:
            import os
            from openai import OpenAI

            client = OpenAI(base_url=profile.base_url, api_key=os.environ[profile.api_key_env], max_retries=8)
        self.client = client
        self.base_url = str(profile.base_url)
        self.model = profile.model
        self.system = system
        self.tools = to_responses_tools(tools)
        self.max_tokens = max_tokens
        self.reasoning_effort = profile.reasoning_effort
        self.stateless = profile.responses_stateless
        self.items: list[dict[str, Any]] = []
        self._result_batches: list[list[dict]] = []

    def add_user(self, text: str) -> None:
        self.items.append({"role": "user", "content": text})

    def step(self, force_tool: str | None = None) -> Step:
        kw: dict[str, Any] = dict(
            model=self.model, instructions=self.system, input=self.items,
            tools=self.tools, max_output_tokens=self.max_tokens,
        )
        if self.reasoning_effort:
            kw["reasoning"] = {"effort": self.reasoning_effort}
        if force_tool:
            kw["tool_choice"] = {"type": "function", "name": force_tool}
        if self.stateless:  # nichts serverseitig speichern; Reasoning verschlüsselt zurückgeben
            kw["store"] = False
            kw["include"] = ["reasoning.encrypted_content"]
        resp = self.client.responses.create(**kw)

        calls, texts = [], []
        for item in resp.output:
            self.items.append(_to_dict(item))  # Reasoning-, Message- und Call-Items unverändert zurück
            if item.type == "function_call":
                try:
                    import json
                    args = json.loads(item.arguments or "{}")
                except ValueError:
                    args = {}
                calls.append(ToolCall(item.call_id, item.name, args))
            elif item.type == "message":
                for part in item.content:
                    if getattr(part, "type", "") == "output_text":
                        texts.append(part.text)
        u = resp.usage
        return Step(
            text="".join(texts), tool_calls=calls,
            input_tokens=getattr(u, "input_tokens", 0) or 0,
            output_tokens=getattr(u, "output_tokens", 0) or 0,
            model=resp.model, stop_reason=str(getattr(resp, "status", "")),
        )

    def add_tool_results(self, results: list[tuple[str, str, bool]]) -> None:
        batch = [{"type": "function_call_output", "call_id": i, "output": ("FEHLER: " if e else "") + t}
                 for i, t, e in results]
        self._result_batches.append(batch)
        self.items.extend(batch)

    def trim_old_results(self, keep: int) -> None:
        old = self._result_batches[:-keep] if keep > 0 else self._result_batches
        for batch in old:
            for it in batch:
                it["output"] = STUB
