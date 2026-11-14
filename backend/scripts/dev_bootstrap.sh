#!/usr/bin/env sh
set -e
cd "$(dirname "$0")/.."
test -f .env || cp .env.example .env
command -v uv >/dev/null 2>&1 || { echo "Install uv: https://docs.astral.sh/uv/getting-started/installation/"; exit 1; }
uv sync --group dev
echo "Run: make up && make migrate"
