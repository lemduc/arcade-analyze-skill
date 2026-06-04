#!/usr/bin/env bash
# arcade-guard pre-commit hook (blocking tier).
#
# Install:
#   cp assets/guard-pre-commit.sh .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
# Requires:
#   ARCADE_AGENT_HOME  -> your arcade-agent checkout (with .venv)
#   ARCADE_SKILL_DIR   -> this skill's dir (defaults to ~/.claude/skills/arcade-analyze)
#   architecture.spec.json in the repo root
#
# Blocks the commit if the architecture FAILs the contract. Set GUARD_FAIL_ON=never
# to make it advisory (warn but allow).
set -euo pipefail

ARCADE_SKILL_DIR="${ARCADE_SKILL_DIR:-$HOME/.claude/skills/arcade-analyze}"
GUARD_FAIL_ON="${GUARD_FAIL_ON:-error}"

if [[ -z "${ARCADE_AGENT_HOME:-}" ]]; then
  echo "arcade-guard: ARCADE_AGENT_HOME not set — skipping architecture check." >&2
  exit 0
fi

"$ARCADE_AGENT_HOME/.venv/bin/python" "$ARCADE_SKILL_DIR/scripts/guard.py" check "$(git rev-parse --show-toplevel)" \
  --fail-on "$GUARD_FAIL_ON"
