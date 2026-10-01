"""Pipeline-Stufen: review → (fix) → (branch). Aktuell ist nur `review` implementiert."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .config import Config
from .llm import AuditLog, RunStats, run_agent
from .providers import make_conversation
from .tools import Toolbox

PROMPTS = Path(__file__).parent / "prompts"


@dataclass
class ReviewResult:
    profile: str
    report: Path | None
    stats: RunStats


def stage_review(cfg: Config) -> ReviewResult:
    """Read-only Security-Review; schreibt Report + Audit-Log (pro Profil) in cfg.output_dir."""
    if cfg.profile is None:
        raise ValueError("Kein Modell-Profil gesetzt (models.toml, --profile).")
    out = cfg.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    base = f"{cfg.target_repo.resolve().name}-{date.today().isoformat()}-{cfg.profile.name}"

    tools = Toolbox(cfg)
    log = AuditLog(out / f"{base}.audit.jsonl")
    conv = make_conversation(
        cfg.profile,
        system=(PROMPTS / "security_review.md").read_text(encoding="utf-8"),
        tools=tools.schemas(),
        max_tokens=cfg.max_tokens,
    )
    stats = run_agent(
        cfg,
        user_prompt=f"Führe die Security-Review für das Repo '{cfg.target_repo.resolve().name}' durch "
                    "und reiche den Report ein. Nimm dir die Zeit, die du für eine gründliche Prüfung brauchst.",
        tools=tools, log=log, conv=conv,
    )
    report_path = None
    if tools.report is not None:
        report_path = out / f"{base}.security-report.md"
        report_path.write_text(tools.report, encoding="utf-8")
    return ReviewResult(cfg.profile.name, report_path, stats)


def stage_fix(cfg: Config, report: Path) -> None:
    """TODO (nächster Ausbauschritt): Findings aus dem Report beheben.

    Erfordert schreibende Tools (edit_file, begrenzt auf target_repo), Tests als Gate und einen
    Freigabeschritt. Bewusst noch nicht implementiert.
    """
    raise NotImplementedError("Fix-Stufe ist noch nicht implementiert.")


def stage_branch(cfg: Config) -> None:
    """TODO: `git switch -c <branch_prefix>...`, committen, pushen (nie auf main/master)."""
    raise NotImplementedError("Branch-Stufe ist noch nicht implementiert.")


def run(cfg: Config) -> ReviewResult | None:
    result = stage_review(cfg) if cfg.stage_review else None
    if cfg.stage_fix and result and result.report:
        stage_fix(cfg, result.report)
    if cfg.stage_branch:
        stage_branch(cfg)
    return result
