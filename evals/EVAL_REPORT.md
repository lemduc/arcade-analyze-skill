# arcade-guard — Evaluation Report (Phase 6)

**Question.** Does the architecture guardrail actually keep AI agents conformant
to an intended architecture while they build — and does it reliably catch
violations when they occur?

**TL;DR.** Two experiments. (1) Across 12 agent runs (Sonnet + Haiku, with vs.
without the guardrail) on a structured codebase, *every* run stayed conformant —
including the no-guardrail runs — and the guardrail produced **zero false
positives**. (2) When the tempting violations are actually introduced, the
guardrail **catches 3/3 deterministically**, each with the correct rule, the
spec's own rationale, and a fix; the gate exits non-zero. So the headline finding
is nuanced and honest: *on a well-structured codebase capable agents largely
self-comply, so the guardrail's measured value is the **deterministic
guarantee/backstop**, not nudging an already-careful agent.*

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

## Interpretation

- The guardrail's job is to make a violation **impossible to land silently**.
  Experiment 2 shows it does that perfectly on the cases the tasks target.
- Experiment 1 shows current agents often don't *need* nudging on a codebase that
  already encodes its structure — so the headline value is the **deterministic
  backstop + greenfield guidance**, not changing careful behavior. That's an
  honest, useful conclusion: deploy it as the *gate/guarantee*, and expect the
  proactive tools to matter most where there's no structure to imitate.

## Threats to validity (read these)

- **Small N + agent stochasticity.** 12 runs; treat as directional, not a
  benchmark.
- **The seed leaks its own architecture.** The seed files have docstrings like
  "must go through the service layer only," and the existing `api→service→store`
  shape is itself a strong signal — so the "off" agents weren't truly
  architecture-blind. This biases *toward* compliance in the off condition (a
  conservative bias for showing guardrail benefit) and is the main reason
  Experiment 1 shows no delta.
- **Structured, not greenfield.** The plan's premise is *new* software; this seed
  is pre-structured. The condition where the guardrail should most change
  outcomes — greenfield code with no structure to infer — is **not yet tested**.
- **Conformance ≈ structural rules.** We measure forbidden/layer/cycle/budget
  conformance, not functional correctness of the agents' features (spot-checked
  by the agents, not asserted here).

## What this says to do next

1. **Greenfield eval** — start from an empty package + only a spec, give the
   off-agents no structural hint, and measure whether they violate. This is the
   condition the guardrail is really for.
2. **Strip the seed's architecture-revealing docstrings** so the off condition is
   a fair control.
3. **Adversarial tasks** — features whose *only* simple implementation crosses a
   forbidden boundary, to force the choice.
4. **Measure the proactive loop** — log how often `propose`/`preview` change the
   agent's plan, not just the final verdict.

## Reproduce

```bash
# seed copies per task/condition were created under /tmp/eval; agents were run
# via the harness, then:
ARCADE_AGENT_HOME=/path/to/arcade-agent \
  /path/to/arcade-agent/.venv/bin/python evals/score.py \
    off-T1=<dir> on-T1=<dir> ...        # Experiment 1
# Experiment 2: inject a shortcut import into a seed copy, then:
guard.py check <dir> --fail-on error    # expect FAIL, exit 1
```

Harness: `evals/seed/`, `evals/tasks.md`, `evals/score.py`.
