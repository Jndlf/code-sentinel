"""Provider-neutrale Schnittstelle. Jede Implementierung hält ihren eigenen Gesprächsverlauf im
Format ihrer API; die Agent-Schleife (llm.py) kennt nur dieses Interface."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class Step:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    stop_reason: str = ""


class Conversation(ABC):
    base_url: str

    @abstractmethod
    def add_user(self, text: str) -> None: ...

    @abstractmethod
    def step(self, force_tool: str | None = None) -> Step:
        """Ein Modellaufruf. Hängt die Modellantwort an den Verlauf an.
        force_tool: zwingt das Modell, genau dieses Tool aufzurufen (für den erzwungenen Abschluss)."""

    @abstractmethod
    def add_tool_results(self, results: list[tuple[str, str, bool]]) -> None:
        """results: (tool_call_id, Text, is_error)."""

    @abstractmethod
    def trim_old_results(self, keep: int) -> None:
        """Ersetzt Tool-Ergebnisse älterer Runden durch einen Platzhalter (Kosten sparen)."""


STUB = "[Ergebnis gekürzt, um Kosten zu sparen. Bei Bedarf Datei erneut lesen.]"
