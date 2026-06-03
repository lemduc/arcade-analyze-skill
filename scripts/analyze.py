#!/usr/bin/env python3
"""arcade-analyze: run the arcade-agent architecture-recovery pipeline and
generate an interactive HTML report.

This wraps arcade-agent's six-step pipeline (ingest -> parse -> recover ->
detect_smells -> compute_metrics -> visualize) into one command, prints a
concise summary to stdout for the agent to relay, and opens the HTML report.

IMPORTANT: This must be run with arcade-agent's virtualenv interpreter, because
the pipeline depends on tree-sitter, networkx, scipy, numpy, jinja2, etc. that
live in that venv. The skill invokes it as:

    <ARCADE_AGENT_HOME>/.venv/bin/python <skill>/scripts/analyze.py <source> ...

The arcade-agent location is resolved from --arcade-home, then the
ARCADE_AGENT_HOME env var, then a built-in default. We insert <home>/src onto
sys.path directly rather than relying on the editable install, because the
editable .pth can point at a stale path.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# No hardcoded default: arcade-agent lives in a different place on every machine,
# so we resolve it from --arcade-home, then $ARCADE_AGENT_HOME, and error out with
# guidance if neither is set rather than guessing (or leaking a personal path).
DEFAULT_ARCADE_HOME = None


def resolve_home(cli_home: str | None) -> Path:
    candidate = cli_home or os.environ.get("ARCADE_AGENT_HOME") or DEFAULT_ARCADE_HOME
    if not candidate:
        sys.exit(
            "[arcade-analyze] arcade-agent location is not set.\n"
            "  Pass --arcade-home /path/to/arcade-agent, or set the\n"
            "  ARCADE_AGENT_HOME environment variable to your arcade-agent checkout."
        )
    home = Path(candidate).expanduser().resolve()
    if not (home / "src" / "arcade_agent").is_dir():
        sys.exit(
            f"[arcade-analyze] Could not find arcade_agent at {home}/src.\n"
            f"  Pass --arcade-home /path/to/arcade-agent or set ARCADE_AGENT_HOME."
        )
    return home


def open_in_browser(path: Path) -> None:
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
        elif sys.platform.startswith("linux"):
            subprocess.run(["xdg-open", str(path)], check=False)
        elif sys.platform.startswith("win"):
            os.startfile(str(path))  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001 - best-effort, never fail the run
        print(f"[arcade-analyze] Could not auto-open report: {exc}", file=sys.stderr)


def main() -> None:
    p = argparse.ArgumentParser(description="Analyze software architecture with arcade-agent")
    p.add_argument("source", help="Path to a source directory or a git URL")
    p.add_argument("--language", "-l", default=None,
                   help="Language: java, python, c, cpp. Auto-detected if omitted.")
    p.add_argument("--algorithm", "-a", default="pkg",
                   help="Recovery algorithm: pkg (default), wca, acdc, arc, limbo")
    p.add_argument("--num-clusters", "-n", type=int, default=None,
                   help="Target cluster count (for wca/acdc/arc/limbo)")
    p.add_argument("--output", "-o", default=None,
                   help="Output HTML path. Default: ./arcade-report/<name>-<algo>.html")
    p.add_argument("--use-llm", action="store_true",
                   help="Use Claude CLI for semantic concern/smell analysis (slower).")
    p.add_argument("--also-mermaid", action="store_true",
                   help="Also emit a Mermaid (.md) component diagram next to the HTML.")
    p.add_argument("--no-open", action="store_true", help="Do not auto-open the report.")
    p.add_argument("--arcade-home", default=None,
                   help="Path to the arcade-agent repo (overrides ARCADE_AGENT_HOME).")
    args = p.parse_args()

    home = resolve_home(args.arcade_home)
    sys.path.insert(0, str(home / "src"))

    # Imports happen after sys.path is set up.
    from arcade_agent.tools.compute_metrics import compute_metrics
    from arcade_agent.tools.detect_smells import detect_smells
    from arcade_agent.tools.ingest import ingest
    from arcade_agent.tools.parse import parse
    from arcade_agent.tools.recover import recover
    from arcade_agent.tools.visualize import visualize

    print(f"[1/6] Ingesting {args.source} ...", flush=True)
    repo = ingest(args.source, language=args.language)
    print(f"      {len(repo.source_files)} source files | language={repo.language} | version={repo.version}")
    if not repo.source_files:
        sys.exit("[arcade-analyze] No source files found. Check the path/language.")

    print("[2/6] Parsing dependencies ...", flush=True)
    graph = parse(str(repo.path), language=repo.language,
                  files=[str(f) for f in repo.source_files])
    print(f"      {graph.num_entities} entities, {graph.num_edges} edges")
    if graph.num_entities == 0:
        sys.exit("[arcade-analyze] No entities extracted. Nothing to recover.")

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

    # Resolve output path (default into a ./arcade-report/ folder in the cwd).
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

    # Machine-readable summary block so the agent can relay results cleanly.
    summary = {
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
    }
    print("\n===ARCADE_SUMMARY_JSON===")
    print(json.dumps(summary, indent=2, default=str))
    print("===END_ARCADE_SUMMARY_JSON===")

    print(f"\nReport: {html_path}")
    repo.cleanup()

    if not args.no_open:
        open_in_browser(Path(html_path))


if __name__ == "__main__":
    main()
