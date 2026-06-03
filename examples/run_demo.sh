#!/usr/bin/env bash
#
# run_demo.sh — regenerate the arcade-analyze demo artifacts.
#
# Runs the arcade-analyze pipeline on two real codebases and writes the HTML
# reports, Mermaid component diagrams, and machine-readable summaries into this
# examples/ directory:
#
#   1. arcade-agent itself (Python)  — dogfood
#   2. arcade_core (Java)            — the canonical USC ARCADE example
#
# Requirements:
#   - ARCADE_AGENT_HOME : path to your arcade-agent checkout (has .venv + src/).
#   - ARCADE_CORE_HOME  : path to an arcade_core checkout (Java). Optional;
#                         defaults to a sibling ../arcade_core of ARCADE_AGENT_HOME.
#                         Clone with: git clone https://github.com/usc-softarch/arcade_core
#
# Usage:
#   ARCADE_AGENT_HOME=/path/to/arcade-agent ./examples/run_demo.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ANALYZE="$SCRIPT_DIR/../scripts/analyze.py"

if [[ -z "${ARCADE_AGENT_HOME:-}" ]]; then
  echo "ERROR: set ARCADE_AGENT_HOME to your arcade-agent checkout." >&2
  echo "  e.g. ARCADE_AGENT_HOME=/path/to/arcade-agent ./examples/run_demo.sh" >&2
  exit 1
fi

PY="$ARCADE_AGENT_HOME/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  echo "ERROR: arcade-agent venv python not found at $PY" >&2
  echo "  Create it in arcade-agent with: python3 -m venv .venv && pip install -e '.[dev]'" >&2
  exit 1
fi

ARCADE_CORE_HOME="${ARCADE_CORE_HOME:-$ARCADE_AGENT_HOME/../arcade_core}"

# Helper: run one analysis and capture the printed summary JSON to <name>.summary.json.
run_one() {
  local name="$1" source="$2" language="$3"
  echo ">>> Analyzing $name ($language): $source"
  "$PY" "$ANALYZE" "$source" \
    --language "$language" \
    --algorithm pkg \
    --also-mermaid \
    --no-open \
    --output "$SCRIPT_DIR/$name.html" \
    | tee "$SCRIPT_DIR/$name.console.txt"
  # Extract the JSON block between the markers into a clean summary file.
  awk '/===ARCADE_SUMMARY_JSON===/{f=1;next} /===END_ARCADE_SUMMARY_JSON===/{f=0} f' \
    "$SCRIPT_DIR/$name.console.txt" > "$SCRIPT_DIR/$name.summary.json"
  rm -f "$SCRIPT_DIR/$name.console.txt"
  echo ">>> Wrote $name.html, $name.md, $name.summary.json"
  echo
}

# 1. Python dogfood — arcade-agent's own source.
run_one "arcade-agent-python" "$ARCADE_AGENT_HOME/src/arcade_agent" "python"

# 2. Java — arcade_core's main source tree (scoped to avoid libs/ext-tools).
if [[ -d "$ARCADE_CORE_HOME/src/main/java" ]]; then
  run_one "arcade-core-java" "$ARCADE_CORE_HOME/src/main/java" "java"
else
  echo "WARNING: arcade_core not found at $ARCADE_CORE_HOME/src/main/java — skipping Java demo." >&2
  echo "  Set ARCADE_CORE_HOME or clone: git clone https://github.com/usc-softarch/arcade_core" >&2
fi

echo "Demo artifacts written to $SCRIPT_DIR"
