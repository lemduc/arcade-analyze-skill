#!/usr/bin/env python3
"""arcade-analyze system: analyze a multi-module / multi-service system.

Architects increasingly work on systems of modules/services, not monoliths.
This analyzes several roots, gives a per-module health table, and recovers the
**system-level** dependency graph (modules as nodes) by resolving cross-module
imports — surfacing which module is the hub and whether modules form a cycle.

    <ARCADE_AGENT_HOME>/.venv/bin/python analyze_system.py \
        /path/to/moduleA /path/to/moduleB /path/to/moduleC [--language java] [-o system.md]

Cross-module edges are inferred from each entity's imports: an import that
resolves to another module's entity (or package) becomes a module→module edge.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from _common import add_common_args, bootstrap, emit_summary, recover_bundle, smell_name


def _metric(metrics, name):
    for m in metrics:
        if m.name == name:
            return m.value
    return None


def _node_id(name: str) -> str:
    nid = "".join(c if c.isalnum() or c == "_" else "_" for c in name)
    return nid or "mod"


def main() -> None:
    p = argparse.ArgumentParser(description="Analyze a multi-module / microservices system")
    p.add_argument("modules", nargs="+", help="Two or more module/service source paths")
    add_common_args(p)
    p.add_argument("--algorithm", "-a", default="pkg", help="Per-module recovery algorithm")
    p.add_argument("--source-root", default=None, help="Sub-path within each module")
    p.add_argument("--output", "-o", default=None, help="Write the markdown report here too")
    args = p.parse_args()

    if len(args.modules) < 2:
        print("Note: only one module given — this is just a single-module analysis.")

    bootstrap(args.arcade_home)

    modules = []  # list of {name, bundle}
    for path in args.modules:
        print(f"\n=== Module: {path} ===", flush=True)
        bundle = recover_bundle(path, args.language, args.source_root, algorithm=args.algorithm)
        modules.append({"name": bundle["repo"].name, "bundle": bundle})

    # Disambiguate duplicate module names.
    seen: dict[str, int] = {}
    for m in modules:
        base = m["name"]
        if base in seen:
            seen[base] += 1
            m["name"] = f"{base}#{seen[base]}"
        else:
            seen[base] = 0

    # Build global maps: entity FQN -> module, and package -> module.
    fqn_to_mod: dict[str, str] = {}
    pkg_to_mod: dict[str, str] = {}
    for m in modules:
        g = m["bundle"]["graph"]
        for fqn in g.entities:
            fqn_to_mod[fqn] = m["name"]
        for pkg in g.packages:
            if pkg:
                pkg_to_mod.setdefault(pkg, m["name"])

    # Module names as lowercase tokens, for matching against import path segments.
    # Modules are parsed under their own roots, so cross-module FQNs rarely align
    # exactly; but a real import like "arcade_agent.algorithms.clustering" contains
    # the module's own name ("algorithms") as a path segment, which resolves it.
    modname_token = {m["name"].split("#")[0].lower(): m["name"] for m in modules}

    def resolve(imp: str) -> str | None:
        if imp in fqn_to_mod:                      # 1. exact entity FQN
            return fqn_to_mod[imp]
        best = None                                # 2. longest package prefix
        for pkg, mod in pkg_to_mod.items():
            if imp == pkg or imp.startswith(pkg + "."):
                if best is None or len(pkg) > best[0]:
                    best = (len(pkg), mod)
        if best:
            return best[1]
        for seg in imp.split("."):                 # 3. module name as a path segment
            if seg.lower() in modname_token:
                return modname_token[seg.lower()]
        return None

    # Cross-module edges with weights.
    edges: dict[tuple[str, str], int] = {}
    for m in modules:
        src_mod = m["name"]
        for ent in m["bundle"]["graph"].entities.values():
            for imp in (ent.imports or []):
                tgt_mod = resolve(imp)
                if tgt_mod and tgt_mod != src_mod:
                    edges[(src_mod, tgt_mod)] = edges.get((src_mod, tgt_mod), 0) + 1

    # System-level findings.
    fan_in = {m["name"]: 0 for m in modules}
    for (s, t), _w in edges.items():
        fan_in[t] += 1
    cycles = sorted({tuple(sorted((s, t))) for (s, t) in edges if (t, s) in edges})

    # Mermaid system diagram.
    mer = ["```mermaid", "graph TD"]
    for m in modules:
        g = m["bundle"]["graph"]
        mer.append(f'    {_node_id(m["name"])}["{m["name"]}<br/>'
                   f'{g.num_entities} entities, {len(m["bundle"]["arch"].components)} comps"]')
    for (s, t), w in sorted(edges.items()):
        mer.append(f"    {_node_id(s)} -->|{w}| {_node_id(t)}")
    mer.append("```")

    # Markdown report.
    lines = [f"# System Architecture: {len(modules)} modules", "",
             "## Per-module health", "",
             "| Module | Entities | Components | Smells | RCI | TurboMQ |",
             "|--------|----------|------------|--------|-----|---------|"]
    for m in modules:
        b = m["bundle"]
        rci, tmq = _metric(b["metrics"], "RCI"), _metric(b["metrics"], "TurboMQ")
        lines.append(f"| {m['name']} | {b['graph'].num_entities} | "
                     f"{len(b['arch'].components)} | {len(b['smells'])} | "
                     f"{rci:.2f} | {tmq:.2f} |")
    lines += ["", "## System dependency graph", "",
              "Modules as nodes; edge labels are the number of cross-module references.", ""]
    lines += mer
    lines += ["", "## System findings", ""]
    if not edges:
        lines.append("- No cross-module dependencies detected (modules are independent, "
                     "or imports don't resolve across these roots).")
    else:
        hub = max(fan_in.items(), key=lambda x: x[1])
        if hub[1] > 0:
            lines.append(f"- **Hub module:** `{hub[0]}` is depended on by {hub[1]} other module(s).")
        if cycles:
            for a, b in cycles:
                lines.append(f"- ⚠️ **Cyclic module dependency:** `{a}` ↔ `{b}` "
                             "(they can't be built, released, or reasoned about independently).")
        else:
            lines.append("- No cyclic dependencies between modules. 👍")
    lines += ["", "---", "*Generated by arcade-analyze. Cross-module edges are inferred "
              "from import resolution across the given roots.*"]
    md = "\n".join(lines)

    print("\n" + md)
    if args.output:
        out = Path(args.output).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md)
        print(f"\nSystem report written to: {out}")

    emit_summary({
        "command": "system",
        "num_modules": len(modules),
        "modules": [{"name": m["name"],
                     "num_entities": m["bundle"]["graph"].num_entities,
                     "num_components": len(m["bundle"]["arch"].components),
                     "num_smells": len(m["bundle"]["smells"]),
                     "rci": _metric(m["bundle"]["metrics"], "RCI"),
                     "turbomq": _metric(m["bundle"]["metrics"], "TurboMQ")}
                    for m in modules],
        "cross_module_edges": [{"from": s, "to": t, "weight": w}
                               for (s, t), w in sorted(edges.items())],
        "module_cycles": [list(c) for c in cycles],
    })


if __name__ == "__main__":
    main()
