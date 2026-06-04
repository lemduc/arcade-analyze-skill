"""arcade-guard: the architecture-contract engine.

Loads an ``architecture.spec.json`` (the author-defined intended architecture)
and checks a codebase against it *deterministically* — conformance is computed
from the spec's file→component globs and the dependency edges, not from the
clustering recovery (which can vary run to run). This is the shared core behind
the ``guard.py`` CLI and the MCP tools.

Spec shape (see assets/architecture.spec.sample.json):
    {
      "intent": "...",
      "components": [{"name","match","layer"}],
      "layers": ["presentation","application","domain","infrastructure"],
      "allow":  [{"from","to"}],
      "forbid": [{"from","to","why"}],
      "budgets": {max_new_smells,min_turbomq,max_component_entities,max_fan_in,no_cycles}
    }
"""

from __future__ import annotations

import json
import re
from pathlib import Path


def load_spec(path: str | Path) -> dict:
    spec = json.loads(Path(path).read_text())
    spec.setdefault("components", [])
    spec.setdefault("layers", [])
    spec.setdefault("allow", [])
    spec.setdefault("forbid", [])
    spec.setdefault("budgets", {})
    return spec


def _glob_to_re(pattern: str) -> re.Pattern:
    """Translate a path glob (supports ** across separators) to a regex."""
    out, i = [], 0
    while i < len(pattern):
        c = pattern[i]
        if pattern[i:i + 2] == "**":
            out.append(".*")
            i += 2
            if i < len(pattern) and pattern[i] == "/":
                i += 1  # '**/' also matches zero dirs
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def map_entities(graph, spec) -> dict[str, str]:
    """Assign each entity to a spec component by matching its file path against
    component ``match`` globs (first match wins). Unmatched → '(unmapped)'."""
    matchers = [(c["name"], _glob_to_re(c.get("match", "")))
                for c in spec["components"] if c.get("match")]
    ent2comp: dict[str, str] = {}
    for fqn, e in graph.entities.items():
        path = str(e.file_path).replace("\\", "/")
        comp = next((name for name, rx in matchers if rx.match(path)), "(unmapped)")
        ent2comp[fqn] = comp
    return ent2comp


UNMAPPED = "(unmapped)"


def component_edges(graph, ent2comp) -> dict[tuple[str, str], int]:
    """Cross-component edge weights. Edges touching the synthetic UNMAPPED bucket
    are excluded — the contract only governs the components it names."""
    edges: dict[tuple[str, str], int] = {}
    for e in graph.edges:
        s, t = ent2comp.get(e.source), ent2comp.get(e.target)
        if s and t and s != t and s != UNMAPPED and t != UNMAPPED:
            edges[(s, t)] = edges.get((s, t), 0) + 1
    return edges


def _layer_of(spec) -> dict[str, str]:
    return {c["name"]: c.get("layer") for c in spec["components"] if c.get("layer")}


def _find_cycles(comp_edges) -> list[list[str]]:
    """Tangled component groups: one entry per strongly-connected component with
    >1 node. Using SCCs (not every simple cycle) gives one violation per tangle
    instead of dozens of near-duplicates."""
    import networkx as nx
    g = nx.DiGraph()
    g.add_edges_from(comp_edges.keys())
    try:
        return [sorted(scc) for scc in nx.strongly_connected_components(g) if len(scc) > 1]
    except Exception:
        return []


def check_conformance(spec, graph, ent2comp, comp_edges,
                      smells=None, metrics=None, baseline=None) -> list[dict]:
    """Return a list of violations. Each: {id, rule, severity (error|warn),
    message, fix}. ``error`` violations fail the blocking gate."""
    violations: list[dict] = []
    layer_of = _layer_of(spec)
    layer_rank = {ly: i for i, ly in enumerate(spec.get("layers", []))}
    budgets = spec.get("budgets", {})

    def add(rule, sev, msg, fix=""):
        violations.append({"id": f"{rule}:{len(violations)}", "rule": rule,
                           "severity": sev, "message": msg, "fix": fix})

    # 1. Forbidden dependencies (component- or layer-level globs on names/layers).
    def _name_or_layer_match(comp, pat):
        return (_glob_to_re(pat).match(comp) is not None) or (layer_of.get(comp) == pat)

    for rule in spec.get("forbid", []):
        frm, to = rule.get("from", "*"), rule.get("to", "*")
        for (s, t) in comp_edges:
            if _name_or_layer_match(s, frm) and _name_or_layer_match(t, to):
                add("forbidden-dependency", "error",
                    f"{s} → {t} is forbidden" + (f": {rule['why']}" if rule.get("why") else ""),
                    f"Remove the dependency from {s} to {t}, or route it through an allowed component.")

    # 2. Layer direction: dependencies must point inward (rank increases) unless
    #    explicitly allowed. Only enforced when layers are defined.
    if layer_rank:
        allowed_pairs = {(r.get("from"), r.get("to")) for r in spec.get("allow", [])}
        for (s, t) in comp_edges:
            ls, lt = layer_of.get(s), layer_of.get(t)
            if ls in layer_rank and lt in layer_rank and layer_rank[ls] > layer_rank[lt]:
                if (ls, lt) in allowed_pairs or (s, t) in allowed_pairs:
                    continue
                add("layer-violation", "error",
                    f"{s} ({ls}) depends on {t} ({lt}) — an outward/upward dependency",
                    f"Invert the dependency (depend on an abstraction in {ls}) or move the code.")

    # 3. Cycles.
    if budgets.get("no_cycles"):
        for grp in _find_cycles(comp_edges):
            add("dependency-cycle", "error",
                "Cyclic component group (mutually dependent): " + ", ".join(grp),
                "Break the cycle: introduce an interface or invert one edge so the group forms a layer.")

    # 4. Fan-in ceiling.
    if "max_fan_in" in budgets:
        fan_in: dict[str, int] = {}
        for (_s, t) in comp_edges:
            fan_in[t] = fan_in.get(t, 0) + 1
        for comp, fi in fan_in.items():
            if fi > budgets["max_fan_in"]:
                add("max-fan-in", "warn",
                    f"{comp} is depended on by {fi} components (> {budgets['max_fan_in']})",
                    f"Split {comp} or hide it behind a narrower interface.")

    # 5. Component size ceiling.
    if "max_component_entities" in budgets:
        sizes: dict[str, int] = {}
        for comp in ent2comp.values():
            sizes[comp] = sizes.get(comp, 0) + 1
        for comp, n in sizes.items():
            if comp != "(unmapped)" and n > budgets["max_component_entities"]:
                add("max-component-size", "warn",
                    f"{comp} has {n} entities (> {budgets['max_component_entities']})",
                    f"Split {comp} along its sub-responsibilities.")

    # 6. Metric budget (uses recovered metrics — pkg recovery is deterministic).
    if metrics and "min_turbomq" in budgets:
        tmq = next((m.value for m in metrics if m.name == "TurboMQ"), None)
        if tmq is not None and tmq < budgets["min_turbomq"]:
            add("metric-budget", "error",
                f"TurboMQ {tmq:.2f} below floor {budgets['min_turbomq']}",
                "Improve cohesion / reduce inter-component coupling.")

    # 7. New-smell budget vs baseline.
    if smells is not None and "max_new_smells" in budgets:
        base_n = (baseline or {}).get("num_smells")
        if base_n is not None:
            delta = len(smells) - base_n
            if delta > budgets["max_new_smells"]:
                add("smell-budget", "error",
                    f"{delta} new smell(s) since baseline (budget {budgets['max_new_smells']})",
                    "Resolve the newly introduced smell(s) before committing.")

    return violations


def verdict(violations) -> str:
    if any(v["severity"] == "error" for v in violations):
        return "FAIL"
    if violations:
        return "WARN"
    return "PASS"
