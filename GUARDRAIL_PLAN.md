# Development Plan: arcade-guard — an Architecture Guardrail for AI Coding Agents

## Vision

AI agents are great at writing code that *works* and bad at keeping a system
*coherent*. Left alone, an agent will happily add a 14th responsibility to a god
class, import the database layer from the UI, or introduce a dependency cycle —
because nothing in its loop represents the *intended architecture*. Type checkers
guard types; linters guard style; tests guard behavior. **Nothing guards
architecture while the code is being written.**

`arcade-guard` is that missing layer: an architecture conformance engine that an
AI coding agent consults *as it builds new software*, keeping the emerging system
aligned to an author-defined architecture spec and free of decay. It's built on
the existing `arcade-agent` engine (parse → recover → smells → metrics → compare)
and the `arcade-analyze` skill (rules, validation, drift, the explorable report).

**One-line positioning:** *ESLint/type-checker for software architecture, living
inside the agent's loop.*

## The core insight: guide before you catch

Most "architecture governance" tools are **reactive** — they run in CI and tell
you, after the fact, that you broke a rule. For an AI agent that's too late and
too blunt: the agent already wrote the code, and a red X with no fix just makes
it thrash.

The high-value version is **proactive**. Before the agent writes a new module it
asks "where should this live and what may it depend on?", and the guardrail
answers from the spec. The architecture stays clean *by construction*, and the
reactive check becomes a backstop, not the main event. This plan optimizes for
proactive guidance first, reactive blocking second.

Two design rules fall out of "in the agent's loop":
1. **Deterministic checks in the hot loop.** Conformance is computed from the
   *spec + package/dependency structure* (deterministic), not from LLM-based
   clustering (`arc`/`limbo`) which is slow and varies run-to-run. The guardrail
   must give the same answer for the same code, every time.
2. **Fast.** A guardrail consulted dozens of times per session must respond in
   well under a second on a changed file set — which makes incremental parsing
   (long deferred) a hard requirement, not a nice-to-have.

## Decisions (locked)

| Decision | Choice | Implication |
|----------|--------|-------------|
| Primary integration | **MCP tools (on-demand)** | Cross-agent (Claude Code, Cursor, …). Build on arcade-agent's existing MCP server + session store. |
| Enforcement | **Tiered** | Advisory while developing (the agent queries + self-corrects); hard-blocking at commit/CI. |
| Architecture intent | **Author-defined spec** | A reviewed `architecture.spec.json` in the repo is the source of truth, written up front (optionally LLM-assisted) and evolved deliberately. |

## Implementation status (v0.1 — shipped)

The usable core is built and tested in this repo:

| Phase | Status | What shipped |
|-------|--------|--------------|
| 1. Architecture contract | ✅ done | `scripts/_spec.py` (deterministic conformance engine) + `guard.py init` templates (hexagonal/layered/clean/mvc) + `assets/architecture.spec.sample.json` |
| 2. Guardrail tools | ✅ done | `scripts/guard.py` CLI (check/propose/preview/explain/remediate) **and** `scripts/guard_mcp.py` — a real MCP server exposing `check_architecture`, `propose_placement`, `preview_impact`, `explain_violation`, `remediate` |
| 4. Tiered enforcement | ✅ done | advisory (MCP + `assets/guard-claude-hook.md` PostToolUse hook) → blocking (`assets/guard-pre-commit.sh`, `assets/guard-ci.yml`; `guard check --fail-on error` exits ≠0) |
| 5. Remediation | ✅ done (v1) | `remediate` returns ranked fixes (errors first) per violation; LLM/patch-level remediation is the next increment |
| 3. Incremental parsing | ⏳ open | uses arcade-agent's existing whole-graph mtime cache (re-run on unchanged code is cheap); true file-level incremental merge is the remaining core work |
| 6. Evaluation harness | ✅ done | `evals/` — seed + tasks + `score.py`; 3 experiments in `evals/EVAL_REPORT.md`. (1) Structured codebase, 12 runs: no delta, 0 false positives. (2) Injection: catches 3/3 deterministically. (3) **Greenfield**, 28 runs: no-guardrail violates 21% (`api→store`), guardrail 0%. Net: 0 false positives / 0 false negatives. |
| 7. Productization | ⏳ open | pip/MCP one-liner, VS Code, dashboard — future |

Verified on `arcade_core`: with a 5-component spec the guardrail returns **FAIL**
(forbidden dependency, 3 layer violations, a 5-component cyclic group, metric
budget) with a fix per finding; `propose_placement("redis cache for clustering")`
→ component `clustering` (may depend on `domain`); `preview_impact(visualization
→ clustering)` → **would violate**. Conformance is deterministic and gate-able
(exit 1 on FAIL).

## System shape

```
                 ┌──────────────────────────────────────────┐
   AI agent ───► │  arcade-guard MCP tools (advisory tier)   │
 (Claude/Cursor) │  check_architecture · propose_placement   │
                 │  preview_impact · explain_violation ·      │
                 │  remediate · init_spec · update_spec       │
                 └───────────────┬──────────────────────────┘
                                 │ reads
        architecture.spec.json ──┤ (intent)        ┌───────────────┐
        .arcade/baseline.json ───┤ (last good)     │ arcade-agent  │
                                 ▼                  │ parse·recover │
                 ┌──────────────────────────────┐  │ smells·metrics│
                 │ conformance core (deterministic) │ compare       │
                 │ rules + layers + budgets + drift │◄─┘ (engine)    │
                 └───────────────┬──────────────────┘
                                 │ verdict (PASS/WARN/FAIL + fixes)
        ┌────────────────────────┴───────────────────────┐
        ▼ advisory                                        ▼ blocking
  agent self-corrects                          pre-commit hook + CI gate
  (or Claude Code hook                         (PR comment, exit≠0 on FAIL)
   auto-injects findings)
```

## The Architecture Contract (`architecture.spec.json`)

The spec is the heart of the system — it makes architectural intent *executable*.
It extends today's `.arcade-rules.json` from "a list of rules" into a contract:

```jsonc
{
  "intent": "Hexagonal: domain core, ports, adapters. UI never touches persistence.",
  "components": [                         // the intended module map
    { "name": "domain",   "match": "**/domain/**",   "layer": "domain" },
    { "name": "api",      "match": "**/api/**",      "layer": "presentation" },
    { "name": "store",    "match": "**/store/**",    "layer": "infrastructure" }
  ],
  "layers": ["presentation", "application", "domain", "infrastructure"],
  "allow": [                              // allowed dependency edges (deny by default optional)
    { "from": "presentation", "to": "application" },
    { "from": "application",  "to": "domain" },
    { "from": "infrastructure", "to": "domain" }
  ],
  "forbid": [
    { "from": "presentation", "to": "infrastructure", "why": "UI must not reach storage directly" }
  ],
  "budgets": {                            // metric/smell budgets (the decay ceiling)
    "max_new_smells": 0,
    "min_turbomq": 0.45,
    "max_component_entities": 200,
    "max_fan_in": 8,
    "no_cycles": true
  },
  "evolution": { "requires_review": true } // spec changes are deliberate, not silent
}
```

Key properties:
- **Two references, not one.** The guardrail checks conformance to the **spec**
  (intended structure) *and* no-decay from a rolling **baseline** (last good
  state). New software has a spec on day one and accrues a baseline as it grows.
- **Decay vs. evolution.** An intended architectural change is legitimate — it
  updates the spec (reviewed). The guardrail only flags *unintended* drift. This
  distinction is what keeps it from becoming a straitjacket.
- **Glob-based component mapping** (`match`) so it works on greenfield code before
  clustering would find stable components — deterministic and fast.

---

## Phased plan

Effort estimates assume one focused engineer; each phase ends with something
usable. Phases 1–4 are the MVP guardrail; 5–7 are depth and reach.

### Phase 0 — Inventory & reframe  *(done / in place)*
What already exists to build on:
- `arcade-agent`: parse, recover (deterministic `pkg`), `detect_smells`,
  `compute_metrics`, `compare` (A2A drift), MCP server with session store +
  token-budget truncation, baseline save/load.
- `arcade-analyze` skill: `validate.py` (rules + layered check + exit codes),
  `.arcade-rules.json`, `diff_versions.py` (drift + gate thresholds),
  `arch-gate.yml` (CI), `refactor_plan.py`/`explain_component` (remediation
  ingredients).
- **Gap:** no spec contract, no agent-facing MCP guardrail tools, no proactive
  guidance, no incremental (fast) path, no remediation-as-diff.

### Phase 1 — The Architecture Contract  *(~1–2 wks)*
- Design + JSON-schema the `architecture.spec.json` format above; write a loader
  + validator (extends `validate.py`'s rule engine).
- `init_spec`: scaffold a spec for a new project — from a one-paragraph brief
  (LLM-assisted) or from a chosen template (hexagonal / layered / clean / MVC).
- Deterministic conformance core: given code + spec, return structured violations
  (forbidden edge, layer violation, budget breach, cycle) with stable IDs.
- **Deliverable:** `arcade-guard check <path>` (CLI) passes/fails against a spec.
  This is `validate.py` graduated into a contract checker.

### Phase 2 — Guardrail MCP tools (advisory tier)  *(~2–3 wks)* — *the core*
Extend arcade-agent's MCP server with agent-facing, token-efficient tools:
- `check_architecture(paths?)` → compact verdict: `PASS | WARN | FAIL`, the list
  of violations (each with a one-line *fix*), new-smell delta vs baseline, budget
  status. Designed to be cheap to call often.
- **`propose_placement(description | new_file)`** → *the proactive killer tool*:
  "I'm adding a Redis cache client — which component/layer should it live in, and
  what may it depend on?" Answered from the spec, before code is written.
- `preview_impact(diff | planned_change)` → predicted architectural effect of a
  change *before* applying it (new edges, would-be violations).
- `explain_violation(id)` → why it violates intent + the cheapest fix.
- **Deliverable:** an MCP server (`arcade-guard`) any agent can consult; a short
  skill/system-prompt snippet teaching the agent the consult-before-you-build
  habit.

### Phase 3 — Fast / incremental analysis  *(~2–3 wks)* — *the enabler*
The deferred Phase 4c, now mandatory: a guardrail in the loop must be sub-second.
- File-level incremental parser: re-parse only changed files, merge the delta into
  a cached `DependencyGraph` (mtime/hash-keyed, building on the existing parse
  cache). Careful invalidation of cross-file edges.
- Scope conformance to the diff where possible (check the touched components +
  their neighbors, not the whole repo).
- **Deliverable:** `check_architecture` on a changed file set responds in <1s on a
  ~5k-file repo after a warm cache; a latency benchmark in CI.

### Phase 4 — Tiered enforcement + agent harness  *(~1–2 wks)*
- **Advisory tier:** optional Claude Code `PostToolUse` hook that auto-runs
  `check_architecture` after edits and injects WARN findings into the agent's
  context (so even agents that forget to ask get nudged).
- **Blocking tier:** pre-commit hook + CI gate (extend `arch-gate.yml`) that runs
  the contract check with the spec, posts a conformance PR comment, and exits ≠0
  on FAIL. Reuses `validate.py`/`diff` exit codes.
- **Spec-evolution workflow:** `update_spec` (proposes a reviewed spec change when
  an architectural change is intended) so decay ≠ evolution.
- **Deliverable:** a project where the agent is advised live and blocked at the
  boundary, end to end.

### Phase 5 — Actionable remediation  *(~2–3 wks)*
A guardrail the agent can't act on just blocks it. Make every finding fixable:
- `remediate(violation_id)` → the *minimal change* to restore conformance
  (relocate class to component X, invert dependency via interface Y, extract the
  overloaded concern). Built from `refactor_plan` + `explain_component` + an LLM
  pass, returned as a concrete instruction (and, where safe, a patch).
- Rank fixes by effort × blast radius (reuse `refactor_plan.py`).
- **Deliverable:** FAIL verdicts come with a fix the agent can apply and re-check —
  the self-heal loop.

### Phase 6 — Evaluation & hardening  *(~2–3 wks)* — *prove it works*
- Eval harness: give agents identical build tasks *with a spec*, measure
  conformance **with vs. without** the guardrail — violation count, new smells,
  TurboMQ trajectory, and whether the agent converges. (This is the evidence that
  the guardrail actually keeps architecture clean, not just that it runs.)
- Stability tests: small code edits must not flip verdicts (deterministic core).
- False-positive budget: tune to keep advisory noise low enough that agents/humans
  keep trusting it. Telemetry on violation types seen in real sessions.
- **Deliverable:** a benchmark report + a tuned, trusted default config.

### Phase 7 — Reach & productization  *(later)*
- Packaging: `pip install arcade-guard` + one-line MCP registration.
- Surfaces: VS Code inline hints (hover "this import violates the spec"), a GitHub
  App, a trends dashboard (architecture health over the project's life).
- Language breadth is already broad (Java/Python/C/C++/TS-JS/Go) — extend as
  demand dictates (Rust, Kotlin).

---

## Enforcement tiers (how "tiered" plays out)

| Tier | When | Mechanism | Behavior |
|------|------|-----------|----------|
| Proactive | Before writing code | `propose_placement` / `preview_impact` MCP calls | Guides placement & allowed deps |
| Advisory | After each edit | MCP `check_architecture` (agent-initiated) + optional Claude Code hook | WARN findings + fixes injected; agent self-corrects; never blocks |
| Blocking | Commit / PR | pre-commit hook + CI gate | FAIL → exit≠0, PR comment, merge blocked until conformant or spec updated |

## Success metrics
- **Primary:** in the eval, guardrail-on sessions end with ≥X% fewer architecture
  violations and new smells than guardrail-off, on the same tasks.
- **Convergence:** agents resolve flagged violations within N self-correct cycles.
- **Latency:** p95 `check_architecture` < 1s warm on a 5k-file repo.
- **Trust:** false-positive rate low enough that advisory findings aren't ignored.
- **Adoption:** consulted (`propose_placement`/`check_architecture`) > once per task
  on average, not just at the gate.

## Risks & mitigations
- **Latency in the loop** → Phase 3 incremental parsing; diff-scoped checks.
- **Nondeterministic verdicts** (clustering noise) → deterministic, spec+glob+rule
  based core in the hot loop; keep LLM recovery out of conformance.
- **False positives erode trust** → advisory-first, confidence on findings, strong
  remediation, tunable budgets.
- **Spec rot / decay-vs-evolution confusion** → explicit `update_spec` review
  workflow; the guardrail flags only unintended drift.
- **Agent ignores advice** → the blocking tier is the backstop; the hook nudges
  forgetful agents.
- **Greenfield has no components yet** → glob-based component mapping works from
  the first file; budgets/cycles still apply.

## What we reuse vs. build
- **Reuse:** parse/recover/smells/metrics/compare, MCP session store + budget
  truncation, `validate.py` rule engine, `diff_versions` gating, `arch-gate.yml`,
  `refactor_plan`/`explain_component`, baseline save/load.
- **Build:** the `architecture.spec.json` contract + loader, the agent-facing
  guardrail MCP tools (esp. proactive `propose_placement`/`preview_impact`),
  incremental parsing, remediation-as-diff, the eval harness, the
  spec-evolution workflow.

## Critical path
Phase 1 (contract) → Phase 2 (MCP advisory tools, incl. proactive) → Phase 3
(make it fast) → Phase 4 (tiered enforcement). That's the MVP guardrail an agent
can actually use. Phase 5 (remediation) is what makes it *pleasant* to use; Phase
6 (eval) is what makes it *credible*. Build 1→4, prove with 6, then deepen.
