#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
# Broken SSL_CERT_FILE (common after removing system Python) makes uv fail on PyPI.
if [[ -n "${SSL_CERT_FILE:-}" && ! -f "$SSL_CERT_FILE" ]]; then
  echo "Unset SSL_CERT_FILE (missing file: $SSL_CERT_FILE)"
  unset SSL_CERT_FILE
fi
uv sync --group dev --extra cli
uv tool install --editable ".[cli]" --force
LINE="export MINDOS_HOME=\"$ROOT\""
RC="${HOME}/.zshrc"
if [[ -f "$RC" ]] && ! grep -q 'MINDOS_HOME=' "$RC" 2>/dev/null; then
  printf '\n# MindOS CLI\n%s\n' "$LINE" >>"$RC"
  echo "Appended MINDOS_HOME to $RC"
fi
echo ""
echo "Reload shell:  source ~/.zshrc"
echo "Then run:      mindos   (agents + voice in global tool env)"