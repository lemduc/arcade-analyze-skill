# arcade-guard in the Claude Code loop (advisory tier)

Two ways to put the guardrail in an agent's loop. The **proactive** path (the
agent consults the guardrail before/while building) is the high-value one; the
**hook** is a safety net for agents that forget to ask.

## 1. Proactive (recommended) — teach the agent to consult the guardrail

Add to your project `CLAUDE.md` so the agent uses arcade-guard as it builds:

```markdown
## Architecture guardrail

This project has an architecture contract in `architecture.spec.json`, enforced
by arcade-guard. While developing:

- BEFORE adding a new module/class, run:
  `$ARCADE_AGENT_HOME/.venv/bin/python <skill>/scripts/guard.py propose . --intent "<what you're adding>"`
  and place the code in the component it returns, respecting "may depend on".
- BEFORE adding a cross-component dependency, run:
  `guard.py preview . --from <A> --to <B>` and only add it if ALLOWED.
- AFTER a change, run `guard.py check .` and fix any ERROR before moving on.
- If an architectural change is intended, update `architecture.spec.json`
  (it's reviewed) rather than working around the rule.
```

## 2. PostToolUse hook (safety net) — auto-check after edits

Add to `.claude/settings.json`. After the agent edits a file, this runs
`guard.py check` and surfaces the verdict in the transcript (advisory: it does
not block; the commit/CI gate is the hard stop).

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit",
        "hooks": [
          {
            "type": "command",
            "command": "\"$ARCADE_AGENT_HOME/.venv/bin/python\" \"$HOME/.claude/skills/arcade-analyze/scripts/guard.py\" check \"$CLAUDE_PROJECT_DIR\" --fail-on never 2>/dev/null | tail -20"
          }
        ]
      }
    ]
  }
}
```

`--fail-on never` keeps the hook advisory (warns, never blocks the edit). Use the
pre-commit hook / CI gate (`guard-pre-commit.sh`, `guard-ci.yml`) for the blocking
tier.
