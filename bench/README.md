# ArchAgentBench

A benchmark + harness for measuring whether AI coding agents preserve a project's
intended **architecture**, with and without an in-loop guardrail. It is the
apparatus behind the arcade-guard empirical study (see
`publising/arcade-guard-paper/`).

## Idea

Most AI-agent benchmarks score *functional* correctness (HumanEval, SWE-bench).
ArchAgentBench scores **architectural conformance**: an agent builds/extends a
feature, and the result is checked deterministically against an architecture
contract. Each task is designed so the *easy* implementation crosses a forbidden
boundary, so the benchmark can detect when an agent decays the architecture.

## Layout

```
bench/
├── tasks/<id>/
│   ├── task.json              # feature prompt, folder roles, rules, tempted violation
│   ├── seed/                  # starting code (empty scaffold for greenfield)
│   └── architecture.spec.json # the contract (scored against, deterministically)
├── harness/run.py            # runs claude -p per (task x model x condition x rep), scores
└── results/                  # JSONL output (gitignored)
```

## Running

```bash
ARCADE_AGENT_HOME=/path/to/arcade-agent \
  "$ARCADE_AGENT_HOME/.venv/bin/python" bench/harness/run.py \
    --task bench/tasks/gf-orders-write-py \
    --models haiku,sonnet --conditions off,on --reps 10 \
    --out bench/results/run.jsonl
```

- **Agent runner:** headless `claude -p --model <m> --permission-mode bypassPermissions`
  in an isolated temp workdir. `--models` selects capability tiers (haiku, sonnet).
- **Conditions:** `off` (feature + folder roles only), `on` (also the dependency
  rules + the guard `propose`/`preview`/`check` workflow). Both are scored against
  the same canonical contract via the deterministic `scripts/_spec.py` engine.
- **Output:** one JSONL record per run (verdict, violations, entity count, agent
  exit code, wall time), plus an off-vs-on aggregate.

Each run consumes Claude usage; start small (`--reps 1`) before scaling.

## Status

- Harness validated: it reproduces the pilot's discriminating cell automatically
  (haiku, greenfield write: `off` introduces `api → store`, `on` is conformant).
- Next: grow the task set across settings (greenfield/brownfield), styles
  (layered/hexagonal/microservices), and languages (Python/Java/TS/Go); add the
  `advisory` condition for the mechanism ablation; add functional-correctness
  (test) scoring and cost capture.
