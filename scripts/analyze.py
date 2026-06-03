#!/usr/bin/env python3
"""arcade-analyze: run the arcade-agent architecture-recovery pipeline and
generate an interactive HTML report.

This wraps arcade-agent's six-step pipeline (ingest -> parse -> recover ->
detect_smells -> compute_metrics -> visualize) into one command, prints a
concise summary to stdout for the agent to relay, and opens the HTML report.

IMPORTANT: run this with arcade-agent's virtualenv interpreter, because the
pipeline depends on tree-sitter, networkx, scipy, numpy, jinja2, etc. that live
in that venv:

    <ARCADE_AGENT_HOME>/.venv/bin/python <skill>/scripts/analyze.py <source> ...

`<source>` is a local directory OR a git URL (arcade-agent clones it for you).
Arcade-agent location resolves from --arcade-home, then $ARCADE_AGENT_HOME.
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


def main() -> None:
    p = argparse.ArgumentParser(description="Analyze software architecture with arcade-agent")
    p.add_argument("source", help="Local source directory OR a git URL to clone and analyze")
    add_common_args(p)
    p.add_argument("--algorithm", "-a", default="pkg",
                   help="Recovery algorithm: pkg (default), wca, acdc, arc, limbo")
    p.add_argument("--num-clusters", "-n", type=int, default=None,
                   help="Target cluster count (for wca/acdc/arc/limbo)")
    p.add_argument("--source-root", default=None,
                   help="Sub-path to treat as the source root (e.g. src/main/java). "
                        "Auto-detected if omitted.")
    p.add_argument("--output", "-o", default=None,
                   help="Output HTML path. Default: ./arcade-report/<name>-<algo>.html")
    p.add_argument("--use-llm", action="store_true",
                   help="Use Claude CLI for semantic concern/smell analysis (slower).")
    p.add_argument("--also-mermaid", action="store_true",
                   help="Also emit a Mermaid (.md) component diagram next to the HTML.")
    p.add_argument("--no-open", action="store_true", help="Do not auto-open the report.")
    args = p.parse_args()

    bootstrap(args.arcade_home)
    from arcade_agent.tools.compute_metrics import compute_metrics
    from arcade_agent.tools.detect_smells import detect_smells
    from arcade_agent.tools.recover import recover
    from arcade_agent.tools.visualize import visualize

    print(f"[1/6] Ingesting {args.source} ...", flush=True)
    print("[2/6] Parsing dependencies ...", flush=True)
    repo, graph = ingest_and_parse(args.source, args.language, args.source_root)

    print(f"[3/6] Recovering architecture (algorithm={args.algorithm}) ...", flush=True)
    arch = recover(graph, algorithm=args.algorithm, num_clusters=args.num_clusters)
    print(f"      {len(arch.components)} components")

    print(f"[4/6] Detecting smells{' (LLM)' if args.use_llm else ''} ...", flush=True)
    smells = detect_smells(arch, graph, use_llm=args.use_llm)
    print(f"      {len(smells)} smells")

    concerns = None
    if args.use_llm:
        from arcade_agent.algorithms.concern import extract_concerns_llm
        concerns = extract_concerns_llm(arch, graph)

    print("[5/6] Computing metrics ...", flush=True)
    metrics = compute_metrics(arch, graph)

    if args.output:
        out = Path(args.output).expanduser().resolve()
    else:
        out = Path.cwd() / "arcade-report" / f"{repo.name}-{args.algorithm}.html"
    out.parent.mkdir(parents=True, exist_ok=True)

    print("[6/6] Generating HTML report ...", flush=True)
    html_path = visualize(repo.name, repo.version, graph, arch, smells, metrics,
                          output=str(out), concerns=concerns)

    mermaid_path = None
    if args.also_mermaid:
        mermaid_path = out.with_suffix(".md")
        visualize(repo.name, repo.version, graph, arch, smells, metrics,
                  output=str(mermaid_path), concerns=concerns)

    emit_summary({
        "command": "analyze",
        "repo": repo.name,
        "version": repo.version,
        "language": repo.language,
        "algorithm": args.algorithm,
        "num_entities": graph.num_entities,
        "num_edges": graph.num_edges,
        "num_components": len(arch.components),
        "components": [{"name": c.name, "size": len(c.entities)} for c in arch.components],
        "num_smells": len(smells),
        "smells": [
            {"type": s.smell_type, "severity": s.severity, "description": s.description}
            for s in smells
        ],
        "metrics": [{"name": m.name, "value": m.value} for m in metrics],
        "report_html": str(html_path),
        "report_mermaid": str(mermaid_path) if mermaid_path else None,
    })

    print(f"\nReport: {html_path}")
    repo.cleanup()

    if not args.no_open:
        open_in_browser(Path(html_path))


if __name__ == "__main__":
    main()
