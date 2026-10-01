"""Zentrale Konfiguration: welches Modell, welche Limits, worauf der Agent zugreifen darf.

Alles, was den Handlungsspielraum des Agents bestimmt, steht bewusst hier an einer Stelle.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


PROVIDERS = ("anthropic", "openai", "openai-responses")


@dataclass(frozen=True)
class ModelProfile:
    """Ein Modell an einem Endpunkt. Profile stehen in models.toml (ohne Keys!)."""
    name: str
    provider: str                       # "anthropic" | "openai" (Chat Completions) | "openai-responses"
    base_url: str
    model: str
    api_key_env: str = "LANGDOCK_API_KEY"   # NAME der Umgebungsvariable, nicht der Key
    auth: str = "bearer"                # nur anthropic: "bearer" (Authorization) | "x-api-key"
    max_tokens_param: str = "max_completion_tokens"  # nur openai: manche Gateways wollen "max_tokens"
    reasoning_effort: str | None = None  # openai*: "none"|"low"|"medium"|"high"; None = nicht senden
    responses_stateless: bool = False   # nur openai-responses: store=false + verschlüsseltes Reasoning zurückgeben
    tpm_budget: int | None = None       # überschreibt das globale Tokens-pro-Minute-Budget

    def validate(self) -> None:
        if self.provider not in PROVIDERS:
            raise ValueError(f"Profil '{self.name}': provider muss einer von {', '.join(PROVIDERS)} sein.")
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError(f"Profil '{self.name}': base_url fehlt/ungültig ('{self.base_url}'). "
                             "Trage die URL aus der Langdock-Doku in models.toml ein.")
        if not os.getenv(self.api_key_env):
            raise ValueError(f"Profil '{self.name}': Umgebungsvariable {self.api_key_env} ist nicht gesetzt.")


def load_profiles(path: Path) -> dict[str, ModelProfile]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return {n: ModelProfile(name=n, **v) for n, v in data.get("profiles", {}).items()}


@dataclass(frozen=True)
class Config:
    # --- LLM -------------------------------------------------------------------------
    profile: ModelProfile | None = None    # wird von run.py aus models.toml gesetzt
    max_tokens: int = int(os.getenv("SRA_MAX_TOKENS", "8000"))
    max_turns: int = int(os.getenv("SRA_MAX_TURNS", "100"))  # Schutz gegen Endlosschleifen
    # Rate-Limit-Schutz: Eingabe-Tokens pro Minute, bewusst unter dem Provider-Limit (Langdock: 150k).
    # Kostenschutz: harte Obergrenze für die Summe aller Eingabe-Tokens eines Laufs.
    max_input_tokens_total: int = int(os.getenv("SRA_MAX_INPUT_TOKENS", "1000000"))
    # Tool-Ergebnisse älter als N Runden werden aus dem Verlauf durch einen Platzhalter ersetzt.
    keep_tool_results_turns: int = int(os.getenv("SRA_KEEP_RESULT_TURNS", "1"))
    tpm_budget: int = int(os.getenv("SRA_TPM_BUDGET", "110000"))

    # --- Zugriff ---------------------------------------------------------------------
    target_repo: Path = Path(".")          # Das zu prüfende Repo; einziges lesbares Verzeichnis.
    output_dir: Path = Path("reports")     # Einziges beschreibbares Verzeichnis (Reports, Logs).
    max_file_bytes: int = 200_000          # Größere Dateien werden abgeschnitten.
    # Diese Pfad-Bestandteile werden nie gelesen (Secrets, Abhängigkeiten, Git-Interna).
    denied_parts: tuple[str, ...] = (
        ".git", "node_modules", "target", "dist", "__pycache__", ".venv",
    )
    denied_names: tuple[str, ...] = (".env", "id_rsa", "id_ed25519")
    # Bereits vorhandene Reports dürfen dem Agent nicht als Lösung dienen (fairer Vergleich).
    denied_globs: tuple[str, ...] = ("docs/security-review*", "**/*security-report*")
    denied_suffixes: tuple[str, ...] = (".pem", ".key", ".p12", ".jks")

    # --- Pipeline-Stufen (schrittweiser Ausbau) ---------------------------------------
    stage_review: bool = True
    stage_fix: bool = False       # Später: Fixes anwenden. Aktuell nicht implementiert.
    stage_branch: bool = False    # Später: Änderungen auf separaten Branch committen.
    branch_prefix: str = "security-fix/"

    tool_names: tuple[str, ...] = field(
        default=("list_files", "read_file", "grep", "submit_report")
    )
