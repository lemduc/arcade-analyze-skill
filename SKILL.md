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

Run arcade-agent's architecture-recovery pipeline against a codebase and produce
an **interactive HTML report** (component diagram, dependency graph, smells,
metrics) that opens automatically in the browser.

arcade-agent is the user's own tool — a Python successor to USC's ARCADE
workbench (Architecture Recovery, Change, And Decay Evaluator). It parses source
with tree-sitter, recovers a component-level architecture via clustering
algorithms, detects architectural smells, and computes quality metrics.

## When to use

Use this skill any time the goal is understanding or auditing a codebase at the
**architecture / component level** — not line-level edits. Good fits: "map out
how this project is structured", "recover the architecture", "find dependency
cycles / smells", "how modular is this", "give me a diagram of the components",
"compare PKG vs ACDC recovery on this repo".

Not for: editing code, running its test suite, or single-file questions — those
don't need architecture recovery.

## How to run it

Everything goes through one bundled wrapper, `scripts/analyze.py`, which runs the
full six-step pipeline (ingest → parse → recover → detect_smells →
compute_metrics → visualize) and opens the report.

The wrapper **must** be run with arcade-agent's virtualenv interpreter, because
the pipeline depends on packages installed only in that venv (tree-sitter,
networkx, scipy, numpy, jinja2). Invoke it like this:

```bash
ARCADE_HOME=/Users/lemduc/Desktop/side_project_workspace/arcade-agent
"$ARCADE_HOME/.venv/bin/python" \
  "<skill-dir>/scripts/analyze.py" \
  /path/to/the/codebase \
  --language java \
  --algorithm pkg
```

`<skill-dir>` is the directory containing this SKILL.md. The wrapper resolves the
arcade-agent location from `--arcade-home`, then the `$ARCADE_AGENT_HOME`
environment variable (it errors out with guidance if neither is set) — and it
puts `<home>/src` on `sys.path` itself, so it works even though arcade-agent's
editable-install `.pth` points at a stale path. Don't try to `import
arcade_agent` directly or `pip install` anything; just use the venv interpreter
as shown. On this machine arcade-agent lives at
`/Users/lemduc/Desktop/side_project_workspace/arcade-agent`, so set
`ARCADE_HOME` / `ARCADE_AGENT_HOME` to that (as the example above does).

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

Run with `--help` to see them all.

## Workflow

1. **Confirm the target and language.** Identify the codebase path (or git URL).
   If the language is obvious from the files, pass `--language`; otherwise let it
   auto-detect.
2. **Pick the algorithm.** Default to `pkg` — it's fast, deterministic, and needs
   no LLM. Only reach for `wca`/`acdc`/`arc`/`limbo` if the user wants
   semantic/behavioral clustering or asks to compare algorithms (see
   `references/algorithms.md`).
3. **Run the wrapper** with the venv interpreter as shown above. The report opens
   automatically unless `--no-open`.
4. **Relay the results.** The wrapper prints a `===ARCADE_SUMMARY_JSON===` block
   with entity/edge/component counts, every smell (type, severity, description),
   and all metric values. Summarize this in chat: highlight the component
   breakdown, the most severe smells, and what the metrics imply about
   modularity. Link the report path so the user can open it.

## Interpreting the output for the user

- **Components** — the recovered modules and how many entities each holds. Very
  large components relative to the rest are a modularity warning.
- **Smells** — flag `high`-severity ones first. Common types: dependency cycles
  (BDC), concern overload (BCO), scattered functionality (SPF), link overload
  (BUO). Explain the concrete impact, not just the label.
- **Metrics** — `RCI` and `TurboMQ`/`BasicMQ` near 1.0 indicate cohesive,
  well-separated components; low values or high `InterConnectivity` suggest
  tangled boundaries. Treat these as signals, not verdicts.

The HTML report is self-contained except that it loads Mermaid from a CDN to
render the architecture diagram, so the diagram needs internet to draw; all other
report content works offline.

## Comparing algorithms or versions

To compare recovery algorithms, run the wrapper multiple times with different
`-a` values and distinct `-o` paths, then contrast the component counts, smells,
and metrics across the reports. arcade-agent also ships
`examples/compare_algorithms.py` (side-by-side comparison report) and
`scripts/arch_diff.py` (architecture drift vs. a baseline) in its repo — reach
for those for dedicated A/B or drift workflows.
