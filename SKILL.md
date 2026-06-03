---
name: arcade-analyze
description: >-
  Recover and visualize the architecture of a software codebase using
  arcade-agent (the Python ARCADE successor). Use this whenever the user wants
  to understand, recover, map, diagram, or audit the architecture of a project,
  detect architectural smells (dependency cycles, concern overload, scattered
  functionality), compute architecture quality metrics (RCI, TurboMQ,
  connectivity), or get an interactive visual report of how a codebase's
  components fit together. Triggers on requests like "analyze the architecture
  of X", "recover the architecture", "what does this codebase look like
  structurally", "find architectural smells / cycles", "visualize the
  components", "is this code well-modularized", or pointing at a Java / Python /
  C / C++ repo and asking how it's organized. Works on a local directory or a
  git URL.
---

# arcade-analyze

Recover and explore the architecture of a codebase using arcade-agent — a Python
successor to USC's ARCADE workbench (Architecture Recovery, Change, And Decay
Evaluator). It parses source with tree-sitter, recovers a component-level
architecture via clustering, detects architectural smells, computes quality
metrics, and renders an interactive HTML report.

This skill exposes four workflows, each a bundled script in `scripts/`:

| Workflow | Script | Use for |
|----------|--------|---------|
| **Analyze** | `analyze.py` | One codebase → interactive HTML report (components, smells, metrics) |
| **Compare algorithms** | `compare_algorithms.py` | Same codebase under several recovery algorithms, side-by-side |
| **Diff versions** | `diff_versions.py` | Architectural drift between two git refs (added/removed components, metric deltas, new smells) |
| **Query** | `query.py` | Answer questions about the architecture (summarize, explain a component, find relevant code, structured queries) |

## When to use

Use this skill any time the goal is understanding or auditing a codebase at the
**architecture / component level** — not line-level edits. Good fits: "map out
how this project is structured", "recover the architecture", "find dependency
cycles / smells", "how modular is this", "give me a diagram of the components",
"compare PKG vs ACDC recovery", "what changed architecturally since v1.0?",
"which component has the highest fan-in?", "explain the Clustering component".

Not for: editing code, running its test suite, or single-file questions — those
don't need architecture recovery.

## How to run the scripts

Every script **must** run with arcade-agent's virtualenv interpreter, because the
pipeline depends on packages installed only in that venv (tree-sitter, networkx,
scipy, numpy, jinja2). The general form:

```bash
ARCADE_HOME=/Users/lemduc/Desktop/side_project_workspace/arcade-agent
"$ARCADE_HOME/.venv/bin/python" "<skill-dir>/scripts/<script>.py" <args...>
```

`<skill-dir>` is the directory containing this SKILL.md. Each script resolves the
arcade-agent location from `--arcade-home`, then the `$ARCADE_AGENT_HOME`
environment variable (it errors out with guidance if neither is set) — and puts
`<home>/src` on `sys.path` itself, so it works even though arcade-agent's
editable-install `.pth` points at a stale path. Don't try to `import
arcade_agent` directly or `pip install` anything; just use the venv interpreter.
On this machine arcade-agent lives at
`/Users/lemduc/Desktop/side_project_workspace/arcade-agent`, so set `ARCADE_HOME`
/ `ARCADE_AGENT_HOME` to that.

Every script prints a `===ARCADE_SUMMARY_JSON===` … `===END_ARCADE_SUMMARY_JSON===`
block to stdout. Parse that block for the structured result and relay it in chat;
don't try to scrape the human-readable lines.

`<source>` is a local directory **or a git URL** — arcade-agent clones the URL
for you (item 1d). Use this for analyzing repos you don't have locally.

---

## Workflow 1 — Analyze (`analyze.py`)

The default workflow: one codebase → interactive HTML report, auto-opened.

```bash
"$ARCADE_HOME/.venv/bin/python" "<skill-dir>/scripts/analyze.py" \
  /path/to/the/codebase --language java --algorithm pkg
```

### Key options

- `--language / -l` — `java`, `python`, `c`, `cpp`. Auto-detected if omitted, but
  pass it when you know it to avoid mis-detection on polyglot repos.
- `--algorithm / -a` — recovery algorithm. Default `pkg` (package-based, fast,
  no LLM). Others: `wca`, `acdc`, `arc`, `limbo`. See `references/algorithms.md`.
- `--num-clusters / -n` — target component count for `wca`/`acdc`/`arc`/`limbo`.
- `--use-llm` — semantic concern + smell detection via the `claude` CLI. Richer
  but slower; only use when the user wants deeper "what concern does each
  component own" analysis. Set `ARCADE_MOCK=1` to dry-run without LLM calls.
- `--output / -o` — HTML output path. Defaults to
  `./arcade-report/<name>-<algorithm>.html` in the current directory.
- `--also-mermaid` — also write a `.md` Mermaid component diagram next to the
  HTML (useful for pasting a diagram into chat, a README, or a paper).
- `--no-open` — skip auto-opening (use in headless contexts or when batching).

Run with `--help` to see them all. Default to `pkg` — fast, deterministic, no
LLM. The report opens automatically unless `--no-open`. After it runs, relay the
summary JSON in chat (component breakdown, most severe smells, what the metrics
imply about modularity) and link the report path.

---

## Workflow 2 — Compare algorithms (`compare_algorithms.py`)

Recover the same codebase under several algorithms and produce one side-by-side
HTML report. Use when the architect asks "which recovery algorithm fits this
project?" or wants to confirm a "well-modularized" claim across lenses — a
codebase that looks clean under `pkg` (package-based) but tangled under `wca`
(dependency-based) is telling you the package layout hides the real coupling.

```bash
"$ARCADE_HOME/.venv/bin/python" "<skill-dir>/scripts/compare_algorithms.py" \
  /path/to/codebase --language java --algorithms pkg,wca,acdc
```

- `--algorithms` — comma-separated (default `pkg,wca,acdc`, no LLM needed). Add
  `arc`/`limbo` only with `--use-llm`.
- `--num-clusters / -n` — **strongly recommended** when including `wca`: without a
  target count WCA over-fragments (one cluster per entity). Set it to roughly the
  `pkg` component count for a fair comparison.
- The summary JSON lists per-algorithm component count, smell count, RCI, and
  TurboMQ. Contrast them in chat and link the comparison report.

---

## Workflow 3 — Diff versions (`diff_versions.py`)

Quantify architectural drift between two git refs. The script clones the repo to
a temp dir and checks out both refs there, so the user's working tree is never
touched. Use for "what changed architecturally since v1.0?", tracking tech-debt
accrual, or preparing an architecture-review before/after.

```bash
"$ARCADE_HOME/.venv/bin/python" "<skill-dir>/scripts/diff_versions.py" \
  /path/to/LOCAL/git/repo --from v1.0.0 --to v1.2.0 --language java
```

- `<repo>` must be a **local git repository** (it needs the history to check out
  refs). Refs can be tags, branches, or commit SHAs. `--to` defaults to `HEAD`.
- Prints a markdown drift report (A2A similarity, metric deltas, added/removed
  components, entity movements, possible splits/merges, new vs. resolved smells)
  and a summary JSON. Use `-o report.md` to also save the markdown.
- Reading the result: **A2A similarity** near 1.0 means little structural change;
  a low value (e.g. 0.38) signals a major refactor. A large "entity movements"
  count means classes were reorganized across components even if the component
  set looks similar.

---

## Workflow 4 — Query / Q&A (`query.py`)

Answer questions about a recovered architecture without regenerating a report.
This is the back-end for natural-language Q&A: map the architect's question to a
sub-command, run it, and relay the JSON.

```bash
"$ARCADE_HOME/.venv/bin/python" "<skill-dir>/scripts/query.py" <subcommand> /path/to/codebase [args]
```

| Sub-command | Args | Answers |
|-------------|------|---------|
| `summarize` | `[--focus PKG]` | "Give me an overview" / "what's in package X?" — packages, hotspots, entry points |
| `explain` | `<component>` | "Explain the Clustering component" — API surface, dependencies, cohesion |
| `find` | `"<text>"` | "Where is authentication handled?" — ranked relevant entities (architecture-aware) |
| `ask` | `<question>` | structured queries (below) |

`ask` questions: `component_of` (`--entity FQN`), `dependencies` /
`dependents` / `entities` (`--component NAME`), `most_coupled`, `summary`,
`largest`.

**Mapping natural language to a sub-command** (decide, then run one):
- "what does this codebase look like / overview / structure" → `summarize`
- "what's in / drill into package X" → `summarize --focus X`
- "explain / tell me about the X component", "what's X's API" → `explain X`
- "where is X handled", "find code about X", "what's relevant to X" → `find "X"`
- "which component is class Y in" → `ask component_of --entity Y`
- "what does component X depend on / what depends on X" → `ask dependencies` / `ask dependents --component X`
- "biggest components", "most coupled components", "highest fan-in" → `ask largest` / `ask most_coupled`

Parse results are cached by arcade-agent, so repeated questions about the same
codebase don't re-parse — it's cheap to run several sub-commands in a row.

---

## Interpreting the output for the user

- **Components** — the recovered modules and how many entities each holds. Very
  large components relative to the rest are a modularity warning.
- **Smells** — flag `high`-severity ones first. Common types: dependency cycles
  (BDC), concern overload (BCO), scattered functionality (SPF), link overload
  (BUO). Explain the concrete impact, not just the label.
- **Metrics** — `RCI` and `TurboMQ`/`BasicMQ` near 1.0 indicate cohesive,
  well-separated components; low values or high `InterConnectivity` suggest
  tangled boundaries. Treat these as signals, not verdicts.

The HTML reports are self-contained except that they load Mermaid from a CDN to
render the diagram, so the diagram needs internet to draw; all other report
content works offline.
