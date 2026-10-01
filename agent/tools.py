"""Die Tools des Agents. Nur was hier steht, kann der Agent tun.

Phase 1 ist strikt lesend: list_files / read_file / grep auf dem Ziel-Repo.
`submit_report` schreibt ausschließlich in das Ausgabeverzeichnis.
Jeder Pfad wird auf das Zielverzeichnis begrenzt (kein `..`, keine Symlink-Ausbrüche).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from .config import Config

# Schemas, wie sie die Anthropic Messages API erwartet.
TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "list_files": {
        "name": "list_files",
        "description": "Listet Dateien im Ziel-Repo (relativ zur Repo-Wurzel). Optional Glob-Muster.",
        "input_schema": {
            "type": "object",
            "properties": {"glob": {"type": "string", "description": "z. B. 'service/**/*.java'"}},
        },
    },
    "read_file": {
        "name": "read_file",
        "description": "Liest eine Textdatei des Ziel-Repos. Optional Zeilenbereich (1-basiert).",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start_line": {"type": "integer"},
                "end_line": {"type": "integer"},
            },
            "required": ["path"],
        },
    },
    "grep": {
        "name": "grep",
        "description": "Regex-Suche über alle lesbaren Dateien. Gibt 'pfad:zeile: text' zurück (max. 100 Treffer).",
        "input_schema": {
            "type": "object",
            "properties": {"pattern": {"type": "string"}, "glob": {"type": "string"}},
            "required": ["pattern"],
        },
    },
    "submit_report": {
        "name": "submit_report",
        "description": "Reicht den fertigen Markdown-Report ein. Beendet die Analyse. Genau einmal aufrufen.",
        "input_schema": {
            "type": "object",
            "properties": {"markdown": {"type": "string"}},
            "required": ["markdown"],
        },
    },
}


class ToolError(Exception):
    """Wird dem Modell als Fehlertext zurückgegeben (kein Absturz)."""


class Toolbox:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.root = cfg.target_repo.resolve()
        self.report: str | None = None
        self._handlers: dict[str, Callable[..., str]] = {
            "list_files": self.list_files,
            "read_file": self.read_file,
            "grep": self.grep,
            "submit_report": self.submit_report,
        }

    # -- Schnittstelle zur LLM-Schleife ---------------------------------------------
    def schemas(self) -> list[dict[str, Any]]:
        return [TOOL_SCHEMAS[n] for n in self.cfg.tool_names]

    def call(self, name: str, args: dict[str, Any]) -> str:
        if name not in self.cfg.tool_names:
            raise ToolError(f"Tool '{name}' ist nicht freigegeben.")
        try:
            return self._handlers[name](**args)
        except ToolError:
            raise
        except TypeError as e:
            raise ToolError(f"Ungültige Argumente: {e}") from e

    # -- Zugriffsschutz ---------------------------------------------------------------
    def _readable(self, p: Path) -> bool:
        parts = set(p.relative_to(self.root).parts)
        if parts & set(self.cfg.denied_parts):
            return False
        if p.name in self.cfg.denied_names or p.name.startswith(".env"):
            return False
        if p.suffix in self.cfg.denied_suffixes:
            return False
        rel = p.relative_to(self.root)
        return not any(rel.match(g) for g in self.cfg.denied_globs)

    def _resolve(self, rel: str) -> Path:
        p = (self.root / rel).resolve()
        if not p.is_relative_to(self.root):
            raise ToolError("Pfad liegt außerhalb des Ziel-Repos.")
        if p.exists() and not self._readable(p):
            raise ToolError("Zugriff auf diese Datei ist gesperrt.")
        return p

    def _iter_files(self, glob: str | None):
        for p in sorted(self.root.glob(glob or "**/*")):
            if p.is_file() and self._readable(p.resolve()) and p.resolve().is_relative_to(self.root):
                yield p

    # -- Tools ------------------------------------------------------------------------
    def list_files(self, glob: str | None = None) -> str:
        files = [str(p.relative_to(self.root)) for p in self._iter_files(glob)]
        if len(files) > 500:
            return "\n".join(files[:500]) + f"\n... ({len(files) - 500} weitere, Glob eingrenzen)"
        return "\n".join(files) or "(keine Treffer)"

    def read_file(self, path: str, start_line: int | None = None, end_line: int | None = None) -> str:
        p = self._resolve(path)
        if not p.is_file():
            raise ToolError("Datei nicht gefunden.")
        data = p.read_bytes()[: self.cfg.max_file_bytes]
        if b"\0" in data:
            raise ToolError("Binärdatei.")
        lines = data.decode("utf-8", errors="replace").splitlines()
        s = max((start_line or 1) - 1, 0)
        e = end_line or len(lines)
        return "\n".join(f"{i + 1}: {l}" for i, l in enumerate(lines[s:e], start=s))

    def grep(self, pattern: str, glob: str | None = None) -> str:
        try:
            rx = re.compile(pattern)
        except re.error as e:
            raise ToolError(f"Ungültiges Regex: {e}") from e
        hits: list[str] = []
        for p in self._iter_files(glob):
            try:
                text = p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for n, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{p.relative_to(self.root)}:{n}: {line.strip()[:200]}")
                    if len(hits) >= 100:
                        return "\n".join(hits) + "\n... (auf 100 Treffer begrenzt)"
        return "\n".join(hits) or "(keine Treffer)"

    def submit_report(self, markdown: str) -> str:
        self.report = markdown
        return "Report entgegengenommen."
