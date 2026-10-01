#!/usr/bin/env python3
"""CLI:
  python run.py ../p2p-app-redaktionsplan --profile sonnet
  python run.py ../p2p-app-redaktionsplan --profile sonnet,opus,haiku     # Vergleich nacheinander
  python run.py --list-profiles
"""
from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path

from agent.config import Config, load_profiles
from agent.pipeline import run

MODELS_FILE = Path(__file__).parent / "models.toml"


def main() -> int:
    ap = argparse.ArgumentParser(description="CodeSentinel – Security-Review-Agent")
    ap.add_argument("target", nargs="?", type=Path, help="Pfad zum zu prüfenden Repo")
    ap.add_argument("--profile", default="sonnet", help="Profilname(n) aus models.toml, kommagetrennt")
    ap.add_argument("--models-file", type=Path, default=MODELS_FILE)
    ap.add_argument("--out", type=Path, default=Path("reports"))
    ap.add_argument("--list-profiles", action="store_true")
    args = ap.parse_args()

    profiles = load_profiles(args.models_file)
    if args.list_profiles:
        for p in profiles.values():
            print(f"{p.name:10} {p.provider:9} {p.model:32} {p.base_url}")
        return 0
    if not args.target or not args.target.is_dir():
        print(f"Zielverzeichnis nicht gefunden: {args.target}", file=sys.stderr)
        return 2

    names = [n.strip() for n in args.profile.split(",") if n.strip()]
    unknown = [n for n in names if n not in profiles]
    if unknown:
        print(f"Unbekanntes Profil: {', '.join(unknown)}. Verfügbar: {', '.join(profiles)}", file=sys.stderr)
        return 2

    rows = []
    for n in names:
        p = profiles[n]
        cfg = Config(profile=p, target_repo=args.target, output_dir=args.out)
        print(f"\n== {n} | {p.provider} | {p.model} | {p.base_url}")
        print(f"   Ziel: {cfg.target_repo.resolve()} | Tools: {', '.join(cfg.tool_names)}")
        try:
            res = run(cfg)
            s = res.stats
            rows.append((n, p.model, s.turns, s.input_tokens, s.output_tokens, f"{s.seconds:.0f}s",
                         s.end_reason, str(res.report) if res.report else "-"))
        except Exception as e:  # ein fehlerhaftes Profil soll den Vergleich nicht abbrechen
            print(f"   FEHLER: {type(e).__name__}: {e}", file=sys.stderr)
            rows.append((n, p.model, 0, 0, 0, "-", f"error: {type(e).__name__}", "-"))

    print("\nProfil | Modell | Runden | Tokens in | Tokens out | Dauer | Ende | Report")
    for r in rows:
        print(" | ".join(str(x) for x in r))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
