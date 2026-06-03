#!/usr/bin/env python3
"""arcade-analyze compare-algorithms: recover the same codebase with several
recovery algorithms and produce a side-by-side comparison HTML report.

Architects use this to choose a recovery strategy and to sanity-check a
"well-modularized" claim across multiple lenses — a structure that looks clean
under PKG (package-based) but tangled under WCA (dependency-based) is telling
you the package layout hides the real coupling.

    <ARCADE_AGENT_HOME>/.venv/bin/python compare_algorithms.py <source> \
        [--algorithms pkg,wca,acdc] [--language java] [-o report.html]

`<source>` is a local directory or a git URL.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from _common import (
    add_common_args,
    bootstrap,
    emit_summary,
    ingest_and_parse,
    open_in_browser,
)

# Default set: the no-LLM algorithms, so this runs anywhere with no Claude CLI.
# arc/limbo are available too but need LLM concern vectors, so they're opt-in.
DEFAULT_ALGORITHMS = ["pkg", "wca", "acdc"]
LLM_ALGORITHMS = {"arc", "limbo"}


def main() -> None:
    p = argparse.ArgumentParser(description="Compare recovery algorithms side-by-side")
    p.add_argument("source", help="Local source directory OR a git URL")
    add_common_args(p)
    p.add_argument("--algorithms", default=",".join(DEFAULT_ALGORITHMS),
                   help=f"Comma-separated algorithms (default: {','.join(DEFAULT_ALGORITHMS)}). "
                        "Options: pkg, wca, acdc, arc, limbo. arc/limbo need --use-llm.")
    p.add_argument("--num-clusters", "-n", type=int, default=None,
                   help="Target cluster count for wca/acdc/arc/limbo.")
    p.add_argument("--source-root", default=None,
                   help="Sub-path to treat as the source root (e.g. src/main/java).")
    p.add_argument("--output", "-o", default=None,
                   help="Output HTML path. Default: ./arcade-report/<name>-comparison.html")
    p.add_argument("--use-llm", action="store_true",
                   help="Enable LLM concern extraction (required for arc/limbo).")
    p.add_argument("--no-open", action="store_true", help="Do not auto-open the report.")
    args = p.parse_args()

    algorithms = [a.strip().lower() for a in args.algorithms.split(",") if a.strip()]
    if not algorithms:
        sys.exit("[arcade-analyze] No algorithms requested.")
    needs_llm = [a for a in algorithms if a in LLM_ALGORITHMS]
    if needs_llm and not args.use_llm:
        sys.exit(f"[arcade-analyze] Algorithms {needs_llm} need --use-llm "
                 "(Claude CLI concern vectors). Re-run with --use-llm or drop them.")

    bootstrap(args.arcade_home)
    from arcade_agent.exporters.html import AlgorithmResult, export_comparison_html
    from arcade_agent.tools.compute_metrics import compute_metrics
    from arcade_agent.tools.detect_smells import detect_smells
    from arcade_agent.tools.recover import recover

    print(f"[1/2] Ingesting {args.source} ...", flush=True)
    print("[2/2] Parsing dependencies (shared across algorithms) ...", flush=True)
    repo, graph = ingest_and_parse(args.source, args.language, args.source_root)

    results: list = []
    summary_rows: list[dict] = []
    for algo in algorithms:
        print(f"  [{algo.upper()}] recover ...", flush=True)
        kwargs = {}
        if args.num_clusters is not None:
            kwargs["num_clusters"] = args.num_clusters
        arch = recover(graph, algorithm=algo, **kwargs)

        use_llm = args.use_llm and algo in LLM_ALGORITHMS
        smells = detect_smells(arch, graph, use_llm=use_llm)
        metrics = compute_metrics(arch, graph)

        concerns = {}
        if args.use_llm:
            from arcade_agent.algorithms.concern import extract_concerns_llm
            concerns = extract_concerns_llm(arch, graph)

        results.append(AlgorithmResult(
            algorithm=algo, architecture=arch, smells=smells,
            metrics=metrics, concerns=concerns,
        ))
        metric_map = {m.name: m.value for m in metrics}
        summary_rows.append({
            "algorithm": algo,
            "num_components": len(arch.components),
            "num_smells": len(smells),
            "RCI": metric_map.get("RCI"),
            "TurboMQ": metric_map.get("TurboMQ"),
            "metrics": [{"name": m.name, "value": m.value} for m in metrics],
        })
        print(f"        {len(arch.components)} components, {len(smells)} smells", flush=True)

    if args.output:
        out = Path(args.output).expanduser().resolve()
    else:
        out = Path.cwd() / "arcade-report" / f"{repo.name}-comparison.html"
    out.parent.mkdir(parents=True, exist_ok=True)

    print("Generating comparison report ...", flush=True)
    html_path = export_comparison_html(repo.name, repo.version, graph, results, out)

    emit_summary({
        "command": "compare-algorithms",
        "repo": repo.name,
        "version": repo.version,
        "language": repo.language,
        "num_entities": graph.num_entities,
        "num_edges": graph.num_edges,
        "algorithms": summary_rows,
        "report_html": str(html_path),
    })

    print(f"\nComparison report: {html_path}")
    repo.cleanup()

    if not args.no_open:
        open_in_browser(Path(html_path))


if __name__ == "__main__":
    main()
