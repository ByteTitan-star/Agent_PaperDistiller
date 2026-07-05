#!/usr/bin/env bash
# Bootstrap dev tools and pre-commit hooks for Agent PaperDistiller.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if command -v uv >/dev/null 2>&1; then
  # Dev-only sync avoids heavy runtime deps (chromadb/onnxruntime) on older macOS.
  uv sync --no-default-groups --group dev --no-install-project || true
  uv pip install ruff mypy bandit pre-commit pytest pytest-asyncio pytest-cov types-PyYAML 2>/dev/null || true
else
  python3 -m venv .venv
  .venv/bin/pip install ruff mypy bandit pre-commit pytest pytest-asyncio pytest-cov types-PyYAML
fi

.venv/bin/pre-commit install --hook-type pre-commit --hook-type commit-msg
echo "Done. Run: .venv/bin/pre-commit run --all-files"
