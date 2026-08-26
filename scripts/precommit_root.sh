#!/usr/bin/env bash
# Resolve Agent_PaperDistiller-main root when pre-commit runs from a parent git repo.
set -euo pipefail

find_project_root() {
  local gitroot
  gitroot="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"

  if [ -f "$gitroot/pyproject.toml" ] && [ -d "$gitroot/backend" ]; then
    echo "$gitroot"
    return
  fi
  if [ -f "$gitroot/Agent_PaperDistiller-main/pyproject.toml" ]; then
    echo "$gitroot/Agent_PaperDistiller-main"
    return
  fi
  echo "$gitroot"
}

ROOT="$(find_project_root)"
cd "$ROOT"
exec "$@"
