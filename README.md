# CodeSentinel

Eigenständiger Agent, der ein Repo statisch auf Sicherheitslücken prüft und einen Markdown-Report erzeugt.
Endziel: in der Pipeline Review → Report → Fixes → separater Branch. Ausbau schrittweise.

## Status

| Stufe | Status |
|---|---|
| `review` (Report erzeugen) | implementiert, nur lesend |
| `fix` (Lücken beheben) | Platzhalter (`pipeline.stage_fix`) |
| `branch` (Commit auf eigenem Branch) | Platzhalter (`pipeline.stage_branch`) |

## Nachvollziehbarkeit: Worauf greift der Agent zu, womit arbeitet er?

| Frage | Antwort | Wo im Code |
|---|---|---|
| Welches LLM? | Das per `--profile` gewählte Profil aus `models.toml` (Standard: `sonnet`) | `models.toml`, `agent/config.py` |
| Welche API / welcher Endpunkt? | Pro Profil `provider` (`anthropic`, `openai` = Chat Completions oder `openai-responses`) und `base_url`. Eigene Tool-Schleife, kein Framework | `agent/providers/`, `agent/llm.py` |
| Welcher Key? | Nur der NAME der Umgebungsvariable steht im Profil (`api_key_env`), nie der Key | `models.toml` |
| Was darf der Agent tun? | Nur `list_files`, `read_file`, `grep`, `submit_report` | `agent/tools.py`, `Config.tool_names` |
| Welche Dateien sieht er? | Nur das Ziel-Repo; kein `..`, keine `.env`/Keys, kein `.git`/`node_modules` | `Toolbox._resolve/_readable` |
| Wo schreibt er? | Nur der Report ins `reports/`-Verzeichnis, durch die Pipeline, nicht durch ein Modell-Tool | `pipeline.stage_review` |
| Kann er Befehle ausführen? | Nein. Es gibt kein Shell-Tool. | – |
| Was hat er getan? | Jeder Modell- und Tool-Aufruf steht in `reports/<repo>-<datum>-<profil>.audit.jsonl` (Profil, Modell, Endpunkt, Tokens, Tool, Argumente) | `agent/llm.py: AuditLog` |
| Was wird gesucht? | Checkliste im System-Prompt | `agent/prompts/security_review.md` |

Wichtig: Der Inhalt der gelesenen Dateien geht an den Endpunkt des gewählten Profils (Langdock und der dahinterliegende Modellanbieter). Nur Repos verwenden, bei denen das erlaubt ist.

## Modelle vergleichen

Profile stehen in `models.toml`. Jedes hat Provider, Langdock-URL, Modell-ID und Key-Variable. Beliebig viele möglich:

```bash
python run.py --list-profiles
python run.py ../p2p-app-redaktionsplan --profile sonnet
python run.py ../p2p-app-redaktionsplan --profile sonnet,opus,haiku,openai   # nacheinander, mit Vergleichstabelle
```

Report und Audit-Log werden pro Profil abgelegt (`<repo>-<datum>-<profil>.*`). Am Ende druckt der Lauf eine Tabelle mit
Runden, Tokens, Dauer und Ende-Grund. Ein fehlerhaftes Profil bricht den Vergleich nicht ab.
Reasoning-Modelle mit Tools (z. B. GPT-6-Luna) brauchen `provider = "openai-responses"`; auf Chat Completions gehen Tools nur ohne Reasoning.

## Benutzung

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export LANGDOCK_API_KEY=...         # nie committen; Name kommt aus models.toml (api_key_env)
python run.py ../p2p-app-redaktionsplan --profile sonnet
pytest tests/                        # Zugriffsschutz, offline
```

## Nächste Ausbauschritte

1. **Vergleich:** Agent-Report gegen `p2p-app-redaktionsplan/docs/security-review-2026-09-30.md` halten (Recall der Findings 1–3).
2. **Deterministische Vorstufen:** `gitleaks`, `npm audit`, OWASP Dependency-Check; Ergebnisse als Kontext in den Prompt.
3. **Strukturierte Findings:** `submit_report` durch `submit_findings` (JSON-Schema) ersetzen, Markdown daraus rendern. Nötig für die Fix-Stufe.
4. **Fix-Stufe:** `edit_file`-Tool nur auf einer Git-Arbeitskopie, Tests/Build als Gate, Mensch-Freigabe.
5. **Branch-Stufe:** `security-fix/<datum>`, nie auf `main`, kein Force-Push, PR statt Merge.
6. **Pipeline:** GitHub-Action, Secret `LANGDOCK_API_KEY`, Report als Artifact, Kostenlimit (`max_turns`, Token-Budget).
