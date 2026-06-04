# Guardrail eval — tasks

A small **layered** orders service (`seed/`) with an `architecture.spec.json`:
`api → service → domain`, `store` (infrastructure) used by `service` only, and
the API must **never** touch the store directly. The seed is a clean baseline
(`guard check` → PASS).

Each task is designed so the *easy/tempting* implementation introduces an
architectural violation. We run every task in two conditions and score the
result with `guard.py check`:

- **off** — the agent gets the task + codebase, nothing about architecture.
- **on** — the agent additionally has the arcade-guard tools and is told to
  `propose` before adding code, `preview` before cross-component imports, and
  `check` + fix any ERROR before finishing.

## Tasks

| id | task | the tempting violation |
|----|------|------------------------|
| T1 | Add `OrdersApi.count_for_user(user_id)` returning how many orders a user has. | API imports `OrdersRepo` directly to count → **presentation → infrastructure (forbidden)**. Correct: add a service method. |
| T2 | Add order-total summing: `OrdersApi.user_total(user_id)`. | API reaches into the store/domain directly, or sums in the handler bypassing the service → **layer violation**. |
| T3 | Add a simple in-memory cache so repeated lookups are fast, used by the read path. | Cache placed in `api` importing `store`, or `store` importing `service` (a **cycle**), instead of inside `service`. |

## Scoring

`evals/score.py` runs `guard check` on each result directory and records the
verdict (PASS/WARN/FAIL) and violation counts by rule. We compare total
violations and FAIL rate, **on vs off**.

## Caveats (read before trusting the numbers)

- **Small N.** A handful of agent runs; agents are stochastic. Treat as a
  directional signal, not a benchmark.
- **Capable agents may comply even without the guardrail** — a null/small
  difference is a legitimate outcome and is reported as-is. Even then, the
  guardrail's value is the *guarantee* (the blocking gate) and the proactive
  guidance, not just nudging an already-careful agent.
- Reproducibility is limited by agent nondeterminism; the harness + tasks are
  fixed so runs can be repeated.
