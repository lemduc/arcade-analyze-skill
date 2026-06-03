# Roadmap: arcade-analyze for Software Architects

This roadmap tracks what needs to be built to turn `arcade-analyze` into a daily
tool for professional software architects — not just a proof-of-concept CLI.

**Phases 1, 2, and 3 are complete, plus multi-module (4d)** — see below. The
skill now does single-run analysis, algorithm comparison, version drift, Q&A,
remote repos, executive summaries with a health score, DSM views, C4/Structurizr
export, refactoring roadmaps, rule + layered-architecture validation (with a CI
gate), and multi-module system views. What's left: the deeper arcade-agent core
work in Phase 4 (TypeScript/Go parsers, incremental parsing) and the Phase 5
collaboration features.

Each phase below adds a meaningful capability. Items are marked as:
- `[arcade-agent]` — needs new or extended code in the tool library
- `[skill]` — needs new or extended logic in this skill (the Claude Code layer)
- `[both]` — needs work in both

---

## Gap analysis: current state vs. architect needs

| Architect need | Status |
|----------------|--------|
| Single-run analysis: components, smells, metrics | ✅ done |
| Interactive HTML report, auto-opened | ✅ done |
| Python, Java, C/C++ languages | ✅ done |
| Algorithm comparison (PKG vs WCA vs ACDC etc.) | ✅ done (`scripts/compare_algorithms.py`) |
| Architecture drift across git versions | ✅ done (`scripts/diff_versions.py`) |
| Remote git URL analysis | ✅ done (all scripts accept a git URL as `<source>`) |
| Natural-language Q&A about the architecture | ✅ done (`scripts/query.py`: summarize/explain/find/ask) |
| C4 model export (Context / Container / Component) | ✅ done (`scripts/export_c4.py`: C4-PlantUML + Structurizr) |
| Layer / pattern validation (hexagonal, clean arch) | ✅ done (`scripts/validate.py` layered check) |
| Custom architectural rule checking | ✅ done (`scripts/validate.py` + `.arcade-rules.json`) |
| Stakeholder / executive-grade report | ✅ done (`scripts/summary_report.py`: health score + narrative) |
| Refactoring priority roadmap | ✅ done (`scripts/refactor_plan.py`) |
| Multi-module / microservices system view | ✅ done (`scripts/analyze_system.py`) |
| CI/CD architecture drift gate | ✅ done (`assets/arch-gate.yml` + `validate`/`diff` exit codes) |
| Architecture Decision Record (ADR) generation | ❌ Phase 5 |
| TypeScript / JavaScript support | ✅ done (arcade-agent `parsers/typescript.py`) |
| Go support | ✅ done (arcade-agent `parsers/go.py`) |
| Incremental parsing for large repos | ⏳ Phase 4c — deferred (core cache work) |

---

## Phase 1 — Unlock what already exists ✅ DONE

**Theme:** three real architect workflows were hiding inside arcade-agent's tools
and example scripts but weren't reachable from the skill. Surfacing them required
no new algorithms — just wiring up existing code.

**Status:** shipped. Four scripts in `scripts/`: `analyze.py`,
`compare_algorithms.py`, `diff_versions.py`, `query.py` (sharing `_common.py`).
Verified end-to-end on arcade-agent (Python) and arcade_core (Java) — e.g. the
v1.0.0→v1.2.0 diff reports A2A similarity 0.38 with 902 entity movements and a
dependency cycle that migrated from `Extractors` to `Util`.

### 1a. Algorithm comparison `[skill]` ✅
Shipped as `scripts/compare_algorithms.py` — runs PKG/WCA/ACDC (and ARC/LIMBO
with `--use-llm`) and emits a side-by-side comparison HTML report plus a summary
JSON of per-algorithm components/smells/RCI/TurboMQ.

Useful when: choosing a recovery strategy for a new project; verifying that
a "well-modularised" claim holds across multiple lenses.

### 1b. Architecture drift across git versions `[skill]` ✅
Shipped as `scripts/diff_versions.py` — temp-clones the repo (working tree
untouched), checks out two refs, recovers each, runs `compare()`, and reports:
- Component similarity score (A2A)
- Added / removed components
- Entities that moved between components (refactored classes)
- Delta on all six metrics
- New vs. resolved smells between the versions

Useful when: quantifying technical debt accrual; preparing architecture review
slides; onboarding into "what changed since the rewrite?".

### 1c. Natural-language Q&A mode `[skill]` ✅
Shipped as `scripts/query.py` (summarize / explain / find / ask sub-commands)
driving `summarize()`, `explain_component()`, `find_relevant()`, and `query()`.
The skill maps natural-language questions to a sub-command, e.g.:
- "Which component has the highest fan-in?"
- "Explain what the Clustering component does"
- "What entities are relevant to authentication?"
- "Which component should I look at first to understand how data flows in?"

Instead of a long-lived MCP session, the script relies on arcade-agent's
mtime-based **parse cache** — repeated sub-commands against the same codebase
skip re-parsing, so running several questions in a row is cheap and stateless.

### 1d. Remote git URL analysis `[skill]` ✅
All four scripts accept a git URL as `<source>` (arcade-agent's `ingest()` clones
it), documented in the README and SKILL.md. Architects can analyze repos they
don't have locally — vendor libraries, acquired codebases, dependencies.

---

## Phase 2 — Architect-grade output ✅ DONE

**Shipped at the skill layer** (consuming the recovered architecture, no
arcade-agent core changes needed): `summary_report.py` (2a — health score 0–100,
plain-English findings, recommended actions), `dsm.py` (2b — Design Structure
Matrix HTML with cyclic cells highlighted), `export_c4.py` (2c — C4-PlantUML +
Structurizr DSL), `refactor_plan.py` (2d — severity × blast-radius roadmap,
quick wins vs big bets). Verified on arcade_core: health 0/100 (grade F), DSM
flags the cyclic pairs, C4 emits 13 components / 36 relationships.

**Theme:** the HTML report is a developer dashboard. Architects need output they
can walk into a room with — slides-ready summaries, layered visualisations, and
actionable remediation priorities.

**Effort:** ~3–4 weeks.

### 2a. Stakeholder summary section `[arcade-agent]`
Add a "summary" section to the top of the HTML report:
- One-paragraph plain-English codebase overview (LLM-generated or template-based)
- Health score (weighted composite of RCI + TurboMQ + smell count + smell severity)
- Top 3 findings with plain-English explanations and remediation suggestions
- "Compared to a healthy codebase of similar size" benchmarks (configurable)

The current report is all data, no narrative. An architect needs the narrative to
present to a non-technical audience.

### 2b. Dependency matrix view `[arcade-agent]`
Add a DSM (Design Structure Matrix) view to the HTML report alongside the Mermaid
component diagram. For large systems (13+ components, like arcade_core), a Mermaid
flowchart becomes unreadable. A coloured matrix — rows and columns are components,
cells show dependency weight — scales to 50+ components and immediately reveals
clusters and tangles.

### 2c. C4 component diagram export `[arcade-agent]`
Add a `c4` format to the `visualize` exporter, emitting
[C4-PlantUML](https://github.com/plantuml-stdlib/C4-PlantUML) markup. Maps
recovered components → C4 components, with inter-component dependencies as
relationships. Architects live in C4; this makes the output immediately usable in
architecture documentation.

Also add a `structurizr` export format (JSON DSL) for teams using Structurizr.

### 2d. Refactoring priority roadmap `[skill + arcade-agent]`
After analysis, generate a ranked refactoring plan:
1. Smells ordered by severity × blast radius (how many other components are
   affected)
2. For each smell: the concrete refactoring move (extract class, invert
   dependency, split package), an estimated effort, and the expected metric delta
3. A "quick wins vs. big bets" breakdown

This is the most direct answer to "what should I do about this?"

---

## Phase 3 — Validation and rules ✅ DONE

**Shipped:** `validate.py` reads `.arcade-rules.json` (3a — forbidden-dependency,
no-cycles, metric-gate, max-fan-in, max-component-size) and runs a
layered/clean-architecture heuristic (3b — maps components to layers, flags
upward dependencies); it exits non-zero on violations. CI gate (3c):
`assets/arch-gate.yml` GitHub Action + `assets/arcade-rules.sample.json`, plus
`diff_versions.py --min-similarity / --max-new-smells`. Verified on arcade_core:
4 rule violations correctly flagged, exit code 1.

**Theme:** architects don't just observe architecture — they specify it and
check conformance.

**Effort:** ~3–4 weeks.

### 3a. Architecture rule file `[arcade-agent]`
A new `validate` tool that reads an `.arcade-rules.yml` in the project root and
checks conformance:

```yaml
# .arcade-rules.yml
rules:
  - name: no-presentation-to-data-layer
    type: forbidden-dependency
    from: "**Presentation**"
    to: "**Persistence**"

  - name: max-fan-in
    type: metric-gate
    metric: fan_in
    max: 5

  - name: no-cycles
    type: no-cycles
```

Familiar to architects who use ArchUnit or Checkstyle but without the Java-only
constraint. The skill surfaces rule violations as a dedicated section in the report.

### 3b. Layered architecture detection `[arcade-agent]`
Heuristically detect whether a codebase follows a known pattern (layered,
hexagonal, clean architecture, microkernel) by matching recovered components
against naming and structural signatures. Flag specific layer violations (e.g.
"domain component imports from infrastructure component").

### 3c. CI/CD drift gate (packaged) `[skill + arcade-agent]`
The existing `scripts/arch_diff.py` and `.github/workflows/arch-drift.yml` in
arcade-agent are not packaged for reuse. This item:
- Publishes a standalone GitHub Action to the Marketplace
- Adds a `--fail-on-drift` flag to `arch_diff.py` for use as a pipeline gate
- Adds a JSON threshold file (`.arcade-thresholds.json`) so teams can set their
  own limits on similarity drop, new smells, etc.

---

## Phase 4 — Language and scale breadth (4a, 4b, 4d done; 4c open)

**Theme:** architects work on TypeScript web apps, Go microservices, and Rust
systems — not just Java. These are arcade-agent **core** parser additions.

### 4a. TypeScript / JavaScript — full parser `[arcade-agent]` ✅ DONE
Replaced the stub with a real tree-sitter parser
(`arcade-agent/src/arcade_agent/parsers/typescript.py`): extracts classes,
interfaces, enums, top-level functions, const-arrow functions, and methods, with
import/extends/implements edges. TS imports reference file paths, so relative
specifiers are resolved to the target module to build cross-file edges. Registered
for `.ts/.tsx/.js/.jsx/.mjs/.cjs`; `ingest` and `tree-sitter-typescript` wired up.
Verified on synthetic projects (edges + `implements` resolve) and real repos.
**Known limitation:** the per-entity AST walk makes very large trees (~2k files)
slow — addressed by 4c.

### 4b. Go parser `[arcade-agent]` ✅ DONE
New tree-sitter Go parser (`parsers/go.py`): each directory maps to a Go package
(a component), extracting structs, interfaces, functions, and methods (by
receiver). Cross-package edges come from qualified references (`pkg.Symbol`,
including `qualified_type` in signatures) resolved against imports; intra-package
edges from sibling references. `tree-sitter-go` added to optional deps. Verified
on a synthetic multi-package project (intra- and cross-package edges resolve).
**Note:** verified on synthetic Go; real-world large-repo tuning still pending.

### 4c. Incremental parsing for large repos `[arcade-agent]` ⏳ OPEN
Parsing thousands of files (arcade_core's full tree, or a ~2k-file TS app) takes
tens of seconds to minutes. The mtime-based cache helps after the first run, but
interactive work on large repos needs sub-second re-analysis on changed files.
Implement a file-level incremental parser that re-parses only changed files and
merges the delta into the cached graph. **Deferred:** this is core cache surgery
with real correctness risk (stale edges across files), and the existing
whole-graph cache already covers the common "re-run on unchanged code" case — so
the marginal value is lower than the risk for now.

### 4d. Multi-module / microservices view `[skill]` ✅ DONE
Shipped as `scripts/analyze_system.py`: analyzes several module/service roots,
prints a per-module health table, and recovers the system-level dependency graph
(modules as nodes) by resolving cross-module imports — surfacing the hub module
and any inter-module cycles. Verified across arcade-agent's tools/algorithms/
parsers packages (tools→algorithms→parsers, parsers is the hub, no cycles).

#### Original note — 4d. Multi-module / microservices view `[arcade-agent + skill]`
Architects increasingly analyse systems of services rather than monoliths. Add:
- Multi-root `ingest` (list of repos/directories)
- Cross-module dependency tracking (via API surface matching or shared contract
  files)
- A system-level Mermaid/C4 view showing services as top-level nodes with
  inter-service dependencies

---

## Phase 5 — Collaboration and integration

**Theme:** architecture work is team work. The skill needs to fit into how
architect teams actually share, review, and track findings.

### 5a. Shareable report hosting `[skill]`
After analysis, offer to push the HTML report to a hosted URL (GitHub Gist or
GitHub Pages on the target repo) so the architect can drop a link into a Slack
message or Confluence page. Currently the report lives on a local path that only
the analyst can open.

### 5b. ADR generation from smells `[skill]`
For each high-severity smell, offer to draft an Architecture Decision Record (ADR)
in [MADR](https://adr.github.io/madr/) or [Nygard](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions)
format that documents the problem, the options considered, and a recommended
resolution. ADRs are the standard artifact for capturing architectural decisions
in teams; bridging from "smell detected" to "ADR drafted" closes the last mile.

### 5c. Baseline persistence and trend tracking `[skill + arcade-agent]`
Store a per-project baseline in `.arcade/baseline.json` (arcade-agent already
has this for CI use). Track metric trends across baseline snapshots. When the
skill runs, show "vs. last baseline: RCI −0.05, +2 smells" so architects can see
whether the codebase is improving or degrading over time.

### 5d. VS Code extension `[arcade-agent]`
Surface `explain_component` and `find_relevant` as hover actions and command-palette
commands in VS Code. This is the highest-leverage integration because architects
read code in their IDE — having "what component is this class in, and what does
that component do?" available on hover changes how they navigate unfamiliar
codebases.

---

## Priority order

| Phase | Items | Unlock |
|-------|-------|--------|
| ✅ **Done** (Phase 1) | 1a–1d | Comparison, drift, Q&A, remote repos |
| ✅ **Done** (Phase 2) | 2a–2d | Stakeholder summary, DSM, C4 export, refactoring roadmap |
| ✅ **Done** (Phase 3) | 3a–3c | Rules, layered detection, CI gate |
| ✅ **Done** (Phase 4a) | 4a | TypeScript / JavaScript parser (arcade-agent core) |
| ✅ **Done** (Phase 4b) | 4b | Go parser (arcade-agent core) |
| ✅ **Done** (Phase 4d) | 4d | Multi-module / microservices view |
| **Now** (Phase 4c) | incremental parsing | Sub-second re-analysis on large repos |
| **Later** (Phase 5) | Hosting, ADRs, trends, VS Code | Team-scale collaboration |

Phases 1–3 plus 4d are the "genuinely useful for a software architect" core and
live entirely at the skill layer (riding on arcade-agent's parse + recover +
smells + metrics). Phase 4a (TypeScript) and 4b (Go) extend arcade-agent's
**core** with new tree-sitter parsers — so Java, Python, C/C++, TypeScript/JS,
and Go are now all analysable. The one remaining Phase 4 item, 4c (incremental
parsing), is deferred: it's cache surgery with real correctness risk, and the
existing whole-graph cache already covers the common re-run case.

---

## Relationship to arcade-agent's roadmap

arcade-agent's own roadmap (`arcade-agent/ROADMAP.md`) is focused on making
the tool useful for **AI agents** — token efficiency, MCP protocol, change-aware
context for AI coding assistants. This roadmap is the complementary track:
making it useful for **human software architects**.

The two tracks share infrastructure (parsers, algorithms, exporters) but diverge
on output format and workflow. Most Phase 1–2 items here are skill-layer work
that rides on arcade-agent's existing capabilities; Phases 3–5 need new
arcade-agent features that benefit both tracks.
