# Roadmap: arcade-analyze for Software Architects

This roadmap tracks what needs to be built to turn `arcade-analyze` into a daily
tool for professional software architects — not just a proof-of-concept CLI.

**Phase 1 is complete** (see below): the skill now covers single-run analysis,
algorithm comparison, version drift, and architecture Q&A — plus remote git URLs.
Phases 2–5 below are what's left to make it a full day-to-day architect tool.

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
| C4 model export (Context / Container / Component) | ❌ not started |
| Layer / pattern validation (hexagonal, clean arch) | ❌ not started |
| Custom architectural rule checking | ❌ not started |
| Stakeholder / executive-grade report | ❌ not started |
| Architecture Decision Record (ADR) generation | ❌ not started |
| TypeScript / JavaScript support | stub only in arcade-agent |
| Multi-module / microservices system view | ❌ not started |
| Refactoring priority roadmap | ❌ not started |
| CI/CD architecture drift gate | `scripts/arch_diff.py` exists — not packaged as reusable action |

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

## Phase 2 — Architect-grade output

**Theme:** the current HTML report is a developer dashboard. Architects need
output they can walk into a room with — slides-ready summaries, layered
visualisations, and actionable remediation priorities.

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

## Phase 3 — Validation and rules

**Theme:** architects don't just observe architecture — they specify it and
check conformance. This phase lets them encode their rules and run them as a
validation gate.

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

## Phase 4 — Language and scale breadth

**Theme:** architects work on TypeScript web apps, Go microservices, and Rust
systems — not just Java. Partial support is worse than no support for professional
use.

**Effort:** 4–6 weeks per language.

### 4a. TypeScript / JavaScript — full parser `[arcade-agent]`
The TypeScript parser is a stub in arcade-agent. Complete it with tree-sitter
(imports, class/function declarations, module boundaries). Many modern architectures
are frontend-heavy; an architect needs to analyze the whole system.

### 4b. Go parser `[arcade-agent]`
Go is the dominant language for cloud-native microservices. Package-level
architecture recovery maps cleanly to Go's module/package system. Prioritise over
Rust given frequency in enterprise codebases.

### 4c. Incremental parsing for large repos `[arcade-agent]`
Parsing 5000+ Java files (like arcade_core's full tree) takes tens of seconds.
The mtime-based cache in arcade-agent helps after the first run, but architects
working interactively on large repos need sub-second re-analysis on changed files.
Implement a file-level incremental parser that re-parses only changed files and
merges the delta into the cached graph.

### 4d. Multi-module / microservices view `[arcade-agent + skill]`
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
| ✅ **Done** (Phase 1) | 1a–1d | Surfaced existing arcade-agent tools — comparison, drift, Q&A, remote repos |
| **Now** (Phase 2a–2b) | Stakeholder summary, DSM view | Makes output presentable beyond the team |
| **Next** (Phase 3a–3b) | Rules, layered detection | Moves from observe to validate |
| **Then** (Phase 2c–2d) | C4 export, refactoring roadmap | Full architect deliverable set |
| **Then** (Phase 3c) | Packaged CI drift gate | Operationalises findings |
| **Later** (Phase 4) | TS, Go, incremental, multi-module | Breadth for real-world systems |
| **Later** (Phase 5) | Hosting, ADRs, trends, VS Code | Team-scale collaboration |

The critical path to "genuinely useful for a software architect" runs through
**Phase 1** (unlock existing tools) and **Phase 2a + 2b** (output they can
present). Everything else expands the scope of what's analysable or how it's
shared. An architect with Phase 1–2 coverage can do meaningful work; without
them they have a demo.

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
