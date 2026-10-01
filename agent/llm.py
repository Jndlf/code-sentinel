"""Die Agent-Schleife: Modell fragen → Tool-Aufrufe ausführen → Ergebnisse zurückgeben.

Provider-neutral: spricht nur mit dem `Conversation`-Interface (agent/providers). Jeder Modellaufruf
und jeder Tool-Aufruf wird in ein JSONL-Audit-Log geschrieben (Profil, Modell, Endpunkt, Tokens, Tool, Argumente).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .providers.base import Conversation
from .tools import Toolbox, ToolError


class AuditLog:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._f = path.open("a", encoding="utf-8")

    def write(self, **event) -> None:
        self._f.write(json.dumps({"ts": time.time(), **event}, ensure_ascii=False) + "\n")
        self._f.flush()


@dataclass
class RunStats:
    turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    end_reason: str = ""
    seconds: float = 0.0


def _throttle(window: list[tuple[float, int]], budget: int, log: AuditLog) -> None:
    """Wartet, falls der nächste Aufruf (≈ letzte Eingabegröße + Zuwachs) das TPM-Budget sprengt."""
    now = time.time()
    window[:] = [(t, n) for t, n in window if now - t < 60]
    if not window:
        return
    expected = window[-1][1] * 1.2  # Kontext wächst pro Runde
    while window and sum(n for _, n in window) + expected > budget:
        wait = 60 - (time.time() - window[0][0]) + 1
        if wait <= 0:
            window.pop(0)
            continue
        log.write(event="throttle", wait_s=round(wait, 1))
        print(f"  Rate-Limit-Schutz: warte {wait:.0f}s ...", flush=True)
        time.sleep(wait)
        window[:] = [(t, n) for t, n in window if time.time() - t < 60]


FINAL = ("LETZTE RUNDE: Es sind keine weiteren Lesezugriffe mehr möglich. Rufe jetzt `submit_report` mit dem "
         "vollständigen Report auf Basis deiner bisherigen Erkenntnisse auf. Nicht Geprüftes als solches kennzeichnen.")


def run_agent(cfg: Config, user_prompt: str, tools: Toolbox, log: AuditLog, conv: Conversation) -> RunStats:
    profile = cfg.profile
    budget = (profile.tpm_budget if profile and profile.tpm_budget else cfg.tpm_budget)
    stats = RunStats()
    t0 = time.time()
    window: list[tuple[float, int]] = []  # (Zeitpunkt, Eingabe-Tokens) der letzten 60 s

    conv.add_user(user_prompt)
    log.write(event="start", profile=profile.name if profile else None,
              provider=profile.provider if profile else None,
              model=profile.model if profile else None, base_url=conv.base_url,
              tools=list(cfg.tool_names), target=str(tools.root))

    def finish(reason: str) -> RunStats:
        stats.end_reason, stats.seconds = reason, time.time() - t0
        log.write(event="end", reason=reason, turns=stats.turns,
                  input_tokens=stats.input_tokens, output_tokens=stats.output_tokens)
        return stats

    for turn in range(1, cfg.max_turns + 1):
        remaining = cfg.max_turns - turn
        over_budget = stats.input_tokens >= cfg.max_input_tokens_total
        final = remaining == 0 or over_budget  # letzte Chance: Report erzwingen statt ohne Ergebnis enden
        conv.trim_old_results(cfg.keep_tool_results_turns)
        if final:
            conv.add_user(FINAL)
            log.write(event="forced_report", turn=turn, reason="token_budget" if over_budget else "max_turns")
        _throttle(window, budget, log)

        try:
            step = conv.step(force_tool="submit_report" if final else None)
        except Exception as e:
            if not final:
                raise
            # Manche Modelle/Gateways lehnen ein erzwungenes Tool ab: dann ohne Zwang, nur mit Aufforderung.
            log.write(event="force_failed", error=f"{type(e).__name__}: {e}"[:300])
            step = conv.step()
        stats.turns = turn
        stats.input_tokens += step.input_tokens
        stats.output_tokens += step.output_tokens
        window.append((time.time(), step.input_tokens))
        log.write(event="llm_call", turn=turn, model=step.model, stop_reason=step.stop_reason,
                  input_tokens=step.input_tokens, output_tokens=step.output_tokens)

        if not step.tool_calls:
            return finish("model_stopped_without_tool")

        results = []
        for c in step.tool_calls:
            try:
                out, is_error = tools.call(c.name, c.input), False
            except ToolError as e:
                out, is_error = str(e), True
            log.write(event="tool_call", turn=turn, tool=c.name, args=c.input,
                      error=is_error, result_chars=len(out))
            results.append((c.id, out, is_error))
        conv.add_tool_results(results)

        if final:
            return finish("token_budget_reached" if over_budget else "max_turns_reached")
        if tools.report is not None:
            return finish("report_submitted")

    return finish("max_turns_reached")
