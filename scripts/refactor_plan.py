#!/usr/bin/env python3
"""arcade-analyze refactor-plan: turn detected smells into a ranked action plan.

Answers "what should I actually do about this?" — every smell ranked by
severity × blast radius (how much of the system it touches), each with a
concrete refactoring move, a rough effort estimate, and the metric it should
move. Split into quick wins (high impact / low effort) and big bets.

    <ARCADE_AGENT_HOME>/.venv/bin/python refactor_plan.py <source> \
        [--language java] [-o refactor-plan.md]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from _common import add_common_args, bootstrap, emit_summary, recover_bundle, smell_name

SEVERITY_WEIGHT = {"high": 3.0, "medium": 2.0, "low": 1.0}

# (concrete move, the metric it should improve) per smell type.
MOVE = {
    "Dependency Cycle": ("Introduce an interface or invert one edge to break the loop",
                         "removes the cycle; ↑ TurboMQ, enables independent release"),
    "Concern Overload": ("Split the component along its sub-responsibilities",
                         "↑ cohesion (TurboMQ), smaller blast radius per change"),
    "Scattered Parasitic Functionality": ("Consolidate the scattered concern into one component",
                                          "↓ coupling, one place to change"),
    "Link/Upstream Overload": ("Introduce a facade or split the hub to reduce fan-in",
                               "↓ inter-connectivity, fewer ripple effects"),
}


def _effort(entity_count: int) -> str:
    return "S" if entity_count < 30 else "M" if entity_count < 100 else "L"


def main() -> None:
    p = argparse.ArgumentParser(description="Ranked refactoring roadmap from detected smells")
    p.add_argument("source", help="Local source directory OR a git URL")
    add_common_args(p)
    p.add_argument("--algorithm", "-a", default="pkg", help="Recovery algorithm (default: pkg)")
    p.add_argument("--source-root", default=None, help="Sub-path treated as source root")
    p.add_argument("--use-llm", action="store_true", help="LLM-powered smell analysis")
    p.add_argument("--output", "-o", default=None, help="Write markdown to this path too")
    args = p.parse_args()

    bootstrap(args.arcade_home)
    print(f"Analyzing {args.source} ...", flush=True)
    bundle = recover_bundle(args.source, args.language, args.source_root,
                            algorithm=args.algorithm, use_llm=args.use_llm)
    arch, graph, smells = bundle["arch"], bundle["graph"], bundle["smells"]

    sizes = {c.name: len(c.entities) for c in arch.components}
    ncomp = max(1, len(arch.components))
    deps = arch.component_dependencies(graph)
    fan_in = {c.name: 0 for c in arch.components}
    for src, tgt in deps:
        fan_in[tgt] = fan_in.get(tgt, 0) + 1

    items = []
    for sm in smells:
        label = smell_name(sm)
        affected = sm.affected_components or []
        aff_entities = sum(sizes.get(c, 0) for c in affected)
        # Blast radius: how much of the system this smell spans (0..1+).
        if label == "Dependency Cycle" or label == "Scattered Parasitic Functionality":
            blast = len(affected) / ncomp
        elif label == "Link/Upstream Overload":
            blast = max((fan_in.get(c, 0) for c in affected), default=0) / ncomp
        else:  # Concern Overload — share of the codebase in the affected component
            blast = aff_entities / max(1, graph.num_entities)
        score = round(SEVERITY_WEIGHT.get(sm.severity, 1.0) * (1.0 + 2.0 * blast), 2)
        move, delta = MOVE.get(label, ("Review and refactor", "improves modularity"))
        if getattr(sm, "suggestion", "").strip():
            move = sm.suggestion.strip()
        effort = _effort(aff_entities or sizes.get(affected[0], 30) if affected else 30)
        items.append({
            "smell": label, "severity": sm.severity, "affected": affected,
            "blast_radius": round(blast, 2), "priority": score,
            "effort": effort, "move": move, "expected": delta,
            "description": sm.description or "",
        })

    items.sort(key=lambda x: -x["priority"])
    quick_wins = [i for i in items if i["effort"] in ("S", "M") and i["priority"] >= 3.0]
    big_bets = [i for i in items if i not in quick_wins]

    # Markdown
    lines = [f"# Refactoring Plan: {bundle['repo'].name}", "",
             f"{len(items)} prioritized action(s). Priority = severity × (1 + 2·blast radius). "
             "Effort: S < 30 entities, M < 100, L ≥ 100.", ""]

    def section(title, rows):
        out = [f"## {title}", ""]
        if not rows:
            out += ["_None._", ""]
            return out
        out += ["| # | Priority | Effort | Smell | Where | Move | Expected effect |",
                "|---|----------|--------|-------|-------|------|-----------------|"]
        for i, it in enumerate(rows, 1):
            where = ", ".join(it["affected"]) or "—"
            out.append(f"| {i} | {it['priority']} | {it['effort']} | "
                       f"[{it['severity']}] {it['smell']} | {where} | {it['move']} | {it['expected']} |")
        out.append("")
        return out

    if items:
        lines += section("Quick wins (do first)", quick_wins)
        lines += section("Big bets (plan deliberately)", big_bets)
    else:
        lines += ["No smells detected — no refactoring actions required.", ""]
    lines += ["---", "*Generated by arcade-analyze. Priorities are signals to triage, "
              "not a substitute for engineering judgment.*"]
    md = "\n".join(lines)

    print("\n" + md)
    if args.output:
        out = Path(args.output).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md)
        print(f"\nPlan written to: {out}")

    emit_summary({
        "command": "refactor-plan",
        "repo": bundle["repo"].name,
        "num_actions": len(items),
        "quick_wins": quick_wins,
        "big_bets": big_bets,
    })


if __name__ == "__main__":
    main()
