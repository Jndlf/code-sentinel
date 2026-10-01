#!/bin/sh
# CodeSentinel-Guard: verhindert, dass Security-Reports, Findings, Audit-Logs oder Secrets committet werden.
#
#   scripts/guard.sh          prüft die gestagten Dateien (läuft als pre-commit-Hook, siehe .githooks/)
#   scripts/guard.sh --all    prüft alle getrackten Dateien (läuft in CI, .github/workflows/ci.yml)
#
# Bewusste Ausnahme für eine einzelne Zeile: Kommentar "guard:allow" in dieselbe Zeile schreiben.
# Treffer werden nur mit Datei und Zeilennummer gemeldet, nie mit Inhalt (kein Secret im Terminal-Log).
set -eu

# Dateinamen, die nie ins Repo dürfen.
PATH_RE='(^|/)reports/|(^|/)sentinel-[^/]*$|\.security-report\.md$|(^|/)security-review-[^/]*\.md$|\.audit\.jsonl$|\.findings\.(jsonl|md)$|(^|/)\.env(\..*)?$|\.(pem|key|p12|pfx|jks)$|(^|/)id_(rsa|ed25519|ecdsa)'
ALLOW_PATH_RE='(^|/)\.env\.example$'

# Inhalte: bekannte Key-Formate, private Schlüssel und die Markierung generierter Reports.
SECRET_RE='sk-[A-Za-z0-9_-]{20,}|-----BEGIN ([A-Z]+ )?PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|AKIA[0-9A-Z]{16}|xox[abprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{35}|generated-by:[ ]code-sentinel'

if [ "${1:-}" = "--all" ]; then
  files=$(git ls-files)
  mode=all
else
  files=$(git diff --cached --name-only --diff-filter=ACMR)
  mode=staged
fi

content() {
  if [ "$mode" = all ]; then cat -- "$1"; else git show ":$1"; fi
}

fail=0
OLDIFS=$IFS
IFS='
'
for f in $files; do
  if printf '%s\n' "$f" | grep -Eq "$PATH_RE" && ! printf '%s\n' "$f" | grep -Eq "$ALLOW_PATH_RE"; then
    echo "BLOCKIERT (Dateiname): $f"
    fail=1
    continue
  fi
  lines=$(content "$f" 2>/dev/null | grep -nIE "$SECRET_RE" | grep -v 'guard:allow' | cut -d: -f1 | tr '\n' ' ' || true)
  if [ -n "$lines" ]; then
    echo "BLOCKIERT (Secret oder Report-Inhalt): $f, Zeile(n) $lines"
    fail=1
  fi
done
IFS=$OLDIFS

if [ "$fail" -ne 0 ]; then
  echo ""
  echo "CodeSentinel-Guard: Commit abgebrochen. Reports, Findings, Audit-Logs und Secrets gehören nicht ins Git."
  exit 1
fi
