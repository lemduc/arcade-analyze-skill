# arcade-guard — Evaluation Report (Phase 6)

**Question.** Does the architecture guardrail actually keep AI agents conformant
to an intended architecture while they build — and does it reliably catch
violations when they occur?

**TL;DR.** Three experiments. (1) On a *pre-structured* codebase, 12 agent runs
(Sonnet + Haiku, with vs. without the guardrail) all stayed conformant — even the
no-guardrail runs — with **zero false positives**: capable and small models both
imitate existing structure. (2) When violations are injected, the guardrail
**catches 3/3 deterministically** with the right rule + a fix; the gate exits
non-zero. (3) On **greenfield** code (no structure to imitate), the difference
appears: across 28 greenfield runs the no-guardrail condition introduced the
forbidden `api → store` edge in **3 of 14 runs (21%)** while the guardrail
condition was conformant in **14/14 (0 violations)** — same tasks, same models.
**Net:** zero false positives and zero false negatives across all runs (every
PASS was genuinely conformant, every FAIL a genuine forbidden import — verified
by inspecting the code); the guardrail catches 100% of injected violations and on
greenfield **eliminated** the organic violations a less-careful agent produced
~1-in-5 times unaided.

---

## Setup

- **Seed** (`evals/seed/`): a small layered Python "orders service" —
  `api → service → domain`, with `store` (infrastructure) used by `service` only.
  `architecture.spec.json` forbids `api → store` and keeps the domain pure. The
  seed is a clean baseline (`guard check` → PASS).
- **Tasks** (`evals/tasks.md`): 3 features, each designed so the *easy* path
  introduces a violation — count orders (T1), sum totals (T2), add a lookup cache
  (T3).
- **Conditions:**
  - **off** — agent gets the task + code only; the spec file is **removed** from
    its copy (no explicit architecture signal).
  - **on** — agent additionally has the arcade-guard tools and is told to
    `propose` before coding, `preview` before cross-component imports, and
    `check` + fix any ERROR before finishing.
- **Models:** Sonnet (T-runs) and Haiku (H-runs), to probe whether a *less
  careful* agent behaves differently.
- **Scoring:** `evals/score.py` runs `guard check` on each result and records
  verdict + violations by rule. 12 runs total (3 tasks × 2 conditions × 2 models).

## Experiment 1 — agent conformance, off vs. on

| run | model | verdict | errors | run | model | verdict | errors |
|-----|-------|---------|:-----:|-----|-------|---------|:-----:|
| off-T1 | Sonnet | PASS | 0 | on-T1 | Sonnet | PASS | 0 |
| off-T2 | Sonnet | PASS | 0 | on-T2 | Sonnet | PASS | 0 |
| off-T3 | Sonnet | PASS | 0 | on-T3 | Sonnet | PASS | 0 |
| off-H1 | Haiku  | PASS | 0 | on-H1 | Haiku  | PASS | 0 |
| off-H2 | Haiku  | PASS | 0 | on-H2 | Haiku  | PASS | 0 |
| off-H3 | Haiku  | PASS | 0 | on-H3 | Haiku  | PASS | 0 |

**Aggregate:** off = 6/6 PASS, 0 errors · on = 6/6 PASS, 0 errors.

Every agent — even the no-spec, Haiku runs — routed new code through the service
layer and never reached `api → store`. They inferred the layering from the
existing structure (and, candidly, from the seed's docstrings; see threats) and
followed it.

**Two real takeaways, not a non-result:**
1. **Zero false positives.** The guardrail never flagged conformant code across
   12 runs — important, because a guardrail that cries wolf gets ignored.
2. **On a well-structured codebase, capable *and* small models self-comply.** The
   guardrail's value here is therefore the *guarantee* (it would have caught a
   slip) and proactive guidance, not behavior change on careful agents. Which
   sets up Experiment 2.

## Experiment 2 — does it catch violations when they happen?

We injected each task's tempting shortcut into a clean copy and ran `guard check`:

| injection | what was added | verdict | caught as | gate exit |
|-----------|----------------|:-------:|-----------|:---------:|
| INJ-forbidden | `api` imports `store` directly | **FAIL** | `forbidden-dependency: api → store` ("API must go through the service layer") | 1 |
| INJ-cycle | `store` imports `service` (cache shortcut) | **FAIL** | `layer-violation` + `dependency-cycle: service, store` | 1 |
| INJ-domain | `domain` imports `store` | **FAIL** | `forbidden-dependency: domain → store` ("domain must stay pure") + `dependency-cycle: domain, store` | 1 |

**3/3 caught**, each with the right rule, the spec's own rationale, and a concrete
fix; the blocking gate exits non-zero every time. Detection is deterministic
(same code → same verdict) because conformance is computed from the spec's
file→component globs + dependency edges, not from clustering.

## Experiment 3 — greenfield (no structure to imitate)

Experiment 1's null result came from the seed *encoding its own architecture*
(existing `api→service→store` code + docstrings), which the off-agents copied. So
we re-ran on **greenfield**: an empty scaffold (`app/api|service|store|domain`,
only empty `__init__.py`, no example code, no architecture docstrings). Both
conditions were told the folder *roles* (so output maps to components and is
scorable); only **on** got the dependency *rules* + the `propose`/`preview`/`check`
tools. **off** had no spec (rule-blind). The tasks build an orders feature where
reaching into the store from the API is the natural shortcut.

**3a — breadth** (2 tasks × off/on × Sonnet+Haiku, 8 runs):

| run | model | task | off | on |
|-----|-------|------|:---:|:--:|
| read | Sonnet | list/sum | PASS | PASS |
| write | Sonnet | place/get | PASS | PASS |
| read | Haiku | list/sum | PASS | PASS |
| write | Haiku | place/get | **FAIL** `api→store` | PASS |

**3b — depth** (the discriminating cell, Haiku × greenfield *write*, 10 runs each):

| condition | runs | PASS | FAIL | violation |
|-----------|:----:|:----:|:----:|-----------|
| **off** (rule-blind) | 10 | 8 | **2** | `forbidden-dependency: api → store` |
| **on** (guardrail)   | 10 | 10 | 0 | — |

The two off failures (`off07`, `off08`) both contain
`from app.store... import` **inside the API layer** — the exact forbidden edge;
the passing runs import only the service. The guard distinguishes them
correctly (verified by inspecting the imports).

**Combined greenfield** (3a + 3b):

| condition | runs | violations | rate |
|-----------|:----:|:----------:|:----:|
| **off** | 14 | 3 | **21%** |
| **on**  | 14 | 0 | **0%** |

This is the contrast Experiment 1 couldn't show: with no existing structure to
copy, a less-careful agent reaches into the store from the API about 1 run in 5;
with the guardrail (told `api → store` is FORBIDDEN by `preview`/`propose`) it
did so **zero** times. The capable model (Sonnet) complied in both conditions
even greenfield — so the benefit concentrates on weaker/faster agents and the
harder (write) path.

## Interpretation

- **The guardrail makes a violation impossible to land silently** — Experiment 2
  (3/3 injected caught) and Experiment 3 (every off-violation a true FAIL) show
  detection is sound and deterministic.
- **It changes outcomes exactly where theory predicts.** On a structured codebase
  agents imitate the existing layering and rarely need help (Exp 1: no delta);
  on **greenfield**, where there's nothing to copy, a less-careful agent violates
  ~21% of the time unaided and **0%** with the guardrail (Exp 3). The proactive
  `propose`/`preview` tools matter most precisely when there's no structure to
  infer — which is the plan's actual premise (*new* software).
- **It never false-flags.** Across all runs, every PASS was genuinely conformant
  and every FAIL a genuine forbidden import.
- **Deployment takeaway:** run it as the always-on gate/guarantee; expect the
  largest behavior change on greenfield work and with smaller/faster agents on
  the harder (write/integration) paths.

## Threats to validity (read these)

- **Small N + agent stochasticity.** Largest single cell is 10 runs/condition;
  treat percentages as directional, not a benchmark.
- **Role hints still nudge.** Both conditions were told the folder *roles* (so
  output maps to components and is scorable); that itself pushes toward layering,
  so the off violation rate (21%) is likely a *floor* — a truly hint-free off
  agent would probably violate more. The trade-off is measurability: hint-free
  off-agents invent their own structure and can't be scored against the contract.
- **Capable model rarely slips.** Sonnet complied in every condition incl.
  greenfield, so the measured benefit is concentrated on weaker/faster agents;
  the effect size on frontier models on simple tasks is near zero.
- **Conformance ≈ structural rules.** We measure forbidden/layer/cycle/budget
  conformance, not functional correctness of the features (spot-checked by the
  agents, not asserted here).

## What this says to do next

1. ✅ **Greenfield eval — done** (Experiment 3): off violated 21%, on 0%.
2. **Hint-free off control** — give the off-agents no folder roles at all and
   solve the scoring problem (e.g. score by import target, not component glob) to
   measure the *true* unaided violation rate (expected higher than 21%).
3. **Adversarial tasks** — features whose *only* simple implementation crosses a
   forbidden boundary, to force the choice and raise the effect size.
4. **Scale N + more models/languages** — tighten the percentages with more runs
   per cell and add a TS/Go greenfield round.
5. **Measure the proactive loop** — log how often `propose`/`preview` change the
   agent's plan, not just the final verdict.

## Reproduce

```bash
# Exp 1: copy evals/seed per task/condition, run an agent in each, then score:
ARCADE_AGENT_HOME=/path/to/arcade-agent \
  /path/to/arcade-agent/.venv/bin/python evals/score.py off-T1=<dir> on-T1=<dir> ...
# Exp 2: inject a shortcut import (e.g. `from app.store... import` in app/api) into
#        a seed copy, then:
guard.py check <dir> --fail-on error    # expect FAIL, exit 1
# Exp 3 (greenfield): start from an EMPTY scaffold (app/{api,service,store,domain}/
#        __init__.py only), give off no spec / on the spec + guard tools, run agents
#        on the orders feature, then score the same way.
```

Harness: `evals/seed/` (Exp 1/2 seed), `evals/tasks.md`, `evals/score.py`. The
Exp 3 greenfield scaffold is the same `seed/` with the code files emptied.
