# Recovery algorithms & options

arcade-agent recovers a component-level architecture by clustering source
entities. Pick the algorithm with `--algorithm / -a`. Read this when the user
asks which algorithm to use, wants semantic/behavioral clustering, or wants to
compare algorithms.

## Algorithms

| Value   | Name | How it groups | LLM? | When to use |
|---------|------|---------------|:----:|-------------|
| `pkg`   | Package-based | By package / directory structure | No | **Default.** Fast, deterministic, no LLM. Best first look at any codebase; mirrors how developers already organize the code. |
| `wca`   | Weighted Cluster Analysis | Agglomerative clustering on structural dependency similarity | No | Structure-driven grouping that ignores package boundaries — surfaces de facto modules that cut across packages. Set `-n` for target count. |
| `acdc`  | ACDC | Pattern-based (subgraph dominator / body patterns) | No | Classic pattern recovery; good when the system has clear subsystem hubs. |
| `arc`   | ARC | Concern-based clustering using LLM concern vectors + JS divergence | Yes | Semantic grouping by *what each entity is about*, not just who it calls. Needs `--use-llm`-style Claude access. |
| `limbo` | LIMBO | Information-theoretic clustering with size-weighted JS divergence over concern vectors | Yes | Semantic, balances cluster sizes; good for large systems where ARC over-splits. |

Notes:
- `pkg` and `acdc` derive their own component counts; `wca`/`arc`/`limbo` accept
  `--num-clusters / -n` to set a target.
- `arc` and `limbo` use Claude CLI concern vectors (the Python port replaces the
  original ARCADE's MALLET topic modeling). They are slower and require the
  `claude` CLI to be installed and authenticated. Set `ARCADE_MODEL=haiku` for a
  faster model, or `ARCADE_MOCK=1` to skip real LLM calls during a dry run.
- `--use-llm` separately upgrades **smell** detection (semantic concern overload
  / scattered functionality) regardless of the recovery algorithm.

## Smells (`detect_smells`)

| Code | Smell | Meaning |
|------|-------|---------|
| BDC | Dependency cycle | A group of components depend on each other in a cycle — change ripples and they can't be understood or released independently. |
| BCO | Concern overload | One component owns too many unrelated responsibilities (many entities, few internal links). |
| SPF | Scattered functionality | A single concern is spread thin across many components. |
| BUO | Link overload | A component has an excessive number of inbound/outbound dependencies (a bottleneck/hub). |

Severity is `high` / `medium` / `low`. Surface `high` first when summarizing.

## Metrics (`compute_metrics`)

| Metric | Reading |
|--------|---------|
| RCI | Recovered-vs-conceptual coverage; closer to 1.0 is better. |
| TurboMQ / BasicMQ | Modularization quality — high intra-cluster cohesion, low inter-cluster coupling. Closer to 1.0 is better. |
| IntraConnectivity | Density of dependencies *within* components (higher = more cohesive). |
| InterConnectivity | Density of dependencies *between* components (lower = better separated). |
| TwoWayPairRatio | Fraction of component pairs with bidirectional coupling (lower = fewer tangles). |

Treat metrics as signals to investigate, not pass/fail grades.

## Supported languages

Java, Python, C, C++ have full tree-sitter parsing support. TypeScript/JavaScript
is a stub (limited). Pass `--language` explicitly on polyglot repos to avoid
mis-detection.
