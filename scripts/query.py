#!/usr/bin/env python3
"""arcade-analyze query: explore a recovered architecture to answer questions.

This is the back-end for natural-language Q&A about a codebase. The skill maps
the architect's question to one of these sub-commands; this script does the
structured lookup and prints JSON.

    <ARCADE_AGENT_HOME>/.venv/bin/python query.py summarize <source> [--focus PKG]
    <ARCADE_AGENT_HOME>/.venv/bin/python query.py explain   <source> <component>
    <ARCADE_AGENT_HOME>/.venv/bin/python query.py find      <source> "<text query>"
    <ARCADE_AGENT_HOME>/.venv/bin/python query.py ask        <source> <question> [--entity X] [--component Y]

Sub-commands:
  summarize  Codebase overview (packages, hotspots, entry points) or a package
             drill-down with --focus. No recovery needed; fast.
  explain    Deep dive on one recovered component: API surface, dependencies,
             cohesion. Needs recovery.
  find       Rank entities relevant to a free-text query (architecture-aware).
  ask        Structured query over the recovered architecture. <question> is one
             of: component_of, dependencies, dependents, entities, most_coupled,
             summary, largest.

Parse results are cached by arcade-agent, so repeated questions about the same
codebase are fast (no re-parse).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from _common import add_common_args, bootstrap, emit_summary, ingest_and_parse


def _recover(graph, algorithm: str):
    from arcade_agent.tools.recover import recover
    return recover(graph, algorithm=algorithm)


def cmd_summarize(args) -> dict:
    from arcade_agent.tools.summarize import summarize
    src = args.source
    if args.source_root:
        src = str(Path(args.source) / args.source_root)
    result = summarize(src, language=args.language, focus=args.focus)
    return {"command": "query:summarize", "focus": args.focus, **result}


def cmd_explain(args) -> dict:
    from arcade_agent.tools.explain_component import explain_component
    repo, graph = ingest_and_parse(args.source, args.language, args.source_root)
    arch = _recover(graph, args.algorithm)
    result = explain_component(arch, graph, args.component)
    return {"command": "query:explain", "component": args.component, **result}


def cmd_find(args) -> dict:
    from arcade_agent.tools.find_relevant import find_relevant
    repo, graph = ingest_and_parse(args.source, args.language, args.source_root)
    arch = _recover(graph, args.algorithm)  # enables component-aware boosting
    result = find_relevant(graph, args.query, architecture=arch, top_k=args.top_k)
    return {"command": "query:find", **result}


def cmd_ask(args) -> dict:
    from arcade_agent.tools.query import query
    repo, graph = ingest_and_parse(args.source, args.language, args.source_root)
    arch = _recover(graph, args.algorithm)
    result = query(arch, graph, args.question, entity=args.entity, component=args.component)
    return {"command": "query:ask", "question": args.question, **result}


def main() -> None:
    p = argparse.ArgumentParser(description="Explore a recovered architecture (Q&A back-end)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp, source_help="Local source directory OR a git URL"):
        sp.add_argument("source", help=source_help)
        add_common_args(sp)
        sp.add_argument("--source-root", default=None,
                        help="Sub-path to treat as the source root (e.g. src/main/java).")
        sp.add_argument("--algorithm", "-a", default="pkg",
                        help="Recovery algorithm for component context (default: pkg).")

    sp = sub.add_parser("summarize", help="Codebase overview or package drill-down")
    common(sp)
    sp.add_argument("--focus", default=None, help="Package to drill into (e.g. com.foo.auth)")
    sp.set_defaults(func=cmd_summarize)

    sp = sub.add_parser("explain", help="Explain one recovered component in detail")
    common(sp)
    sp.add_argument("component", help="Component name (as recovered, e.g. Clustering)")
    sp.set_defaults(func=cmd_explain)

    sp = sub.add_parser("find", help="Find entities relevant to a free-text query")
    common(sp)
    sp.add_argument("query", help="Free-text query, e.g. 'authentication login'")
    sp.add_argument("--top-k", type=int, default=10, help="Max results (default: 10)")
    sp.set_defaults(func=cmd_find)

    sp = sub.add_parser("ask", help="Structured query over the architecture")
    common(sp)
    sp.add_argument("question", help="component_of|dependencies|dependents|entities|"
                                     "most_coupled|summary|largest")
    sp.add_argument("--entity", default=None, help="Entity FQN (for component_of)")
    sp.add_argument("--component", default=None,
                    help="Component name (for dependencies/dependents/entities)")
    sp.set_defaults(func=cmd_ask)

    args = p.parse_args()
    bootstrap(args.arcade_home)
    print(f"Exploring {args.source} ({args.cmd}) ...", flush=True)
    result = args.func(args)
    emit_summary(result)


if __name__ == "__main__":
    main()
