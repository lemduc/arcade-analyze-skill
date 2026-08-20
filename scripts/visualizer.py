#!/usr/bin/env python3
"""arcade-analyze visualizer: an app-style architecture explorer.

Where interactive_report.py is a single explorable page, this renders a full
dark-themed single-page app — the closest thing to an "architecture recovery
workbench" for a codebase:

  * Architecture  — pan/zoom node-card diagram (L1 compact / L2 detailed),
                    click a component to drill into a side panel.
  * Dependencies  — weighted edge list + Design Structure Matrix, cycles in red.
  * Failure Points— smells presented as failure cards: severity, concrete
                    impact, suggested mitigation, estimated effort.
  * Simulate      — animate a dependency flow through the component graph with
                    a per-hop waterfall; traces are derived from the graph
                    (entry flow, hub fan-out, cycle walk) and users can record
                    custom traces by clicking nodes.
  * Knowledge     — metrics with plain-English interpretation + smell glossary.
  * Comments      — feedback notes (stored in the browser) that can be copied
                    out as a ready-to-paste prompt for Claude.

    <ARCADE_AGENT_HOME>/.venv/bin/python visualizer.py <source> \
        [--language java] [--algorithm pkg] [-o app.html]

The output is one self-contained HTML file: no CDN, no network, works offline.

For demos/tests the renderer also runs without arcade-agent:

    python3 visualizer.py --from-model model.json -o app.html
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict, deque
from pathlib import Path

from _common import add_common_args, bootstrap, emit_summary, open_in_browser, recover_bundle, smell_name


def _node_id(name: str) -> str:
    nid = "".join(c if c.isalnum() or c == "_" else "_" for c in name)
    return nid or "c"


# Per smell type: how to present it as a failure card. Substring-matched so the
# guide survives small wording differences across arcade-agent versions.
_SMELL_GUIDE = [
    ("cycle", {
        "headline": "Circular dependency",
        "impact": "Changes ripple around the cycle: none of the involved components "
                  "can be built, tested, or understood in isolation, and a refactor "
                  "in one forces retesting all of them.",
        "mitigation": "Break the cycle: extract the shared part into its own component, "
                      "or invert one direction with an interface owned by the callee "
                      "(dependency inversion).",
    }),
    ("concern", {
        "headline": "Component owns too many concerns",
        "impact": "The component accumulates unrelated responsibilities, so unrelated "
                  "changes collide in the same place and its API keeps growing.",
        "mitigation": "Split by concern: group entities that change together and move "
                      "each group behind its own narrow interface.",
    }),
    ("scattered", {
        "headline": "One concern scattered across components",
        "impact": "A single responsibility is implemented in several components, so "
                  "every change to it is a multi-component change and behavior drifts "
                  "between the copies.",
        "mitigation": "Consolidate the concern into one owning component and have the "
                      "others depend on it instead of re-implementing it.",
    }),
    ("link", {
        "headline": "Link overload (too many connections)",
        "impact": "The component is coupled to a large share of the system; most "
                  "changes elsewhere can reach it, making it a stability bottleneck.",
        "mitigation": "Reduce fan-in/fan-out: introduce a facade, narrow the API "
                      "surface, and cut dependencies that only need a small slice of it.",
    }),
]

_DEFAULT_GUIDE = {
    "headline": "Architectural smell",
    "impact": "This structure makes the affected components harder to change safely.",
    "mitigation": "Review the affected components and refactor toward smaller, "
                  "single-concern components with acyclic dependencies.",
}


def _guide_for(smell_type: str) -> dict:
    low = smell_type.lower()
    for key, guide in _SMELL_GUIDE:
        if key in low:
            return guide
    return _DEFAULT_GUIDE


def _effort(severity: str, affected: int) -> str:
    if severity == "high" and affected > 2:
        return "high — multi-component refactor"
    if severity == "high" or affected > 2:
        return "medium — plan across a few PRs"
    return "low — localized change"


def _derive_traces(names: list[str], edges: list[dict]) -> list[dict]:
    """Derive a few interesting flows to animate: an entry-point walk, the
    biggest hub's fan-out walk, and (if the graph has one) a cycle walk."""
    adj: dict[str, list[tuple[str, int]]] = defaultdict(list)
    radj: dict[str, list[str]] = defaultdict(list)
    fan_in: Counter = Counter()
    fan_out: Counter = Counter()
    for e in edges:
        adj[e["source"]].append((e["target"], e["weight"]))
        radj[e["target"]].append(e["source"])
        fan_out[e["source"]] += 1
        fan_in[e["target"]] += 1
    for k in adj:
        adj[k].sort(key=lambda x: -x[1])

    def heavy_walk(start: str) -> list[str]:
        """Longest simple path from start (weight-greedy tie-break), found by a
        DFS with a fixed expansion budget so pathological graphs stay cheap."""
        best = [start]
        budget = [20000]

        def dfs(path: list[str], seen: set[str]) -> None:
            nonlocal best
            if budget[0] <= 0:
                return
            budget[0] -= 1
            if len(path) > len(best):
                best = path[:]
            for t, _w in adj.get(path[-1], []):
                if t not in seen:
                    seen.add(t)
                    path.append(t)
                    dfs(path, seen)
                    path.pop()
                    seen.discard(t)

        dfs([start], {start})
        return best

    traces: list[dict] = []
    used_starts: set[str] = set()

    sources = [n for n in names if fan_in[n] == 0 and fan_out[n] > 0]
    if sources:
        start = max(sources, key=lambda n: fan_out[n])
        path = heavy_walk(start)
        if len(path) >= 2:
            traces.append({
                "id": "entry", "name": "Entry flow",
                "description": f"From entry component {start} (nothing depends on it), "
                               "following the heaviest dependency at each hop.",
                "path": path,
            })
            used_starts.add(start)

    if fan_out:
        hub = max(fan_out, key=lambda n: fan_out[n])
        if hub not in used_starts:
            path = heavy_walk(hub)
            if len(path) >= 2:
                traces.append({
                    "id": "hub", "name": "Hub fan-out walk",
                    "description": f"{hub} has the highest fan-out "
                                   f"({fan_out[hub]} outgoing deps); this walks its "
                                   "heaviest dependency chain.",
                    "path": path,
                })

    # Cycle walk: heaviest mutually-dependent pair, expanded to the shortest
    # cycle through it (BFS back from target to source).
    weight = {(e["source"], e["target"]): e["weight"] for e in edges}
    cyclic = [(s, t) for (s, t) in weight if (t, s) in weight and s < t]
    if cyclic:
        s, t = max(cyclic, key=lambda p: weight[p] + weight[(p[1], p[0])])
        prev, q = {t: None}, deque([t])
        while q:
            cur = q.popleft()
            if cur == s:
                break
            for nxt, _w in adj.get(cur, []):
                if nxt not in prev:
                    prev[nxt] = cur
                    q.append(nxt)
        if s in prev:
            back = [s]
            while back[-1] != t:
                back.append(prev[back[-1]])
            # back = [s, prev[s], ..., t] reconstructs the BFS discovery chain,
            # so reversed it is the directed path t -> ... -> s. Prepend the
            # direct s -> t edge to close the loop: s -> t -> ... -> s.
            traces.append({
                "id": "cycle", "name": "Cycle walk",
                "description": f"{s} and {t} depend on each other. This trace walks "
                               "the cycle once — every hop here is a refactoring hazard.",
                "path": [s] + back[::-1],
            })

    if not traces and edges:
        e = max(edges, key=lambda e: e["weight"])
        traces.append({
            "id": "heaviest", "name": "Heaviest dependency",
            "description": "The single strongest inter-component dependency.",
            "path": [e["source"], e["target"]],
        })
    return traces


def _build_model(bundle, algorithm: str) -> dict:
    from arcade_agent.tools.explain_component import explain_component

    arch, graph, smells, metrics = (bundle["arch"], bundle["graph"],
                                    bundle["smells"], bundle["metrics"])
    total = graph.num_entities or 1
    names = [c.name for c in arch.components]

    # Weighted inter-component edges from the entity-level graph.
    w: Counter = Counter()
    for edge in graph.edges:
        sc, tc = arch.component_of(edge.source), arch.component_of(edge.target)
        if sc and tc and sc != tc:
            w[(sc, tc)] += 1
    edges = [{"source": s, "target": t, "weight": n, "cyclic": (t, s) in w}
             for (s, t), n in sorted(w.items(), key=lambda kv: -kv[1])]

    # Smells → failure cards.
    failures, failures_by_comp = [], defaultdict(list)
    for i, s in enumerate(smells):
        stype = smell_name(s)
        guide = _guide_for(stype)
        affected = list(s.affected_components or [])
        failures.append({
            "id": i,
            "component": affected[0] if affected else "system-wide",
            "type": stype,
            "severity": s.severity,
            "headline": guide["headline"],
            "description": s.description or "",
            "impact": guide["impact"],
            "mitigation": guide["mitigation"],
            "effort": _effort(s.severity, len(affected)),
            "affected": affected,
        })
        for c in affected:
            failures_by_comp[c].append(i)

    fan_in: Counter = Counter()
    fan_out: Counter = Counter()
    for e in edges:
        fan_out[e["source"]] += 1
        fan_in[e["target"]] += 1

    components = []
    for c in arch.components:
        det = explain_component(arch, graph, c.name)
        if "error" in det:
            continue
        entities = det.get("entities", [])
        kinds = Counter(e.get("kind", "entity") for e in entities)
        kind = kinds.most_common(1)[0][0] if kinds else "entity"
        components.append({
            "name": c.name,
            "node_id": _node_id(c.name),
            "kind": kind,
            "responsibility": det.get("responsibility", ""),
            "num_entities": det.get("num_entities", len(c.entities)),
            "share": round(det.get("num_entities", len(c.entities)) / total, 4),
            "cohesion": det.get("cohesion", 0),
            "depends_on": det.get("depends_on", []),
            "depended_on_by": det.get("depended_on_by", []),
            "fan_in": fan_in[c.name],
            "fan_out": fan_out[c.name],
            "api_surface": [f.rsplit(".", 1)[-1] for f in det.get("api_surface", [])][:40],
            "entities": [{"name": e["name"], "kind": e["kind"]} for e in entities][:60],
            "entities_total": len(entities),
            "failure_ids": failures_by_comp.get(c.name, []),
        })

    return {
        "repo": bundle["repo"].name,
        "version": str(bundle["repo"].version),
        "language": bundle["repo"].language,
        "algorithm": algorithm,
        "num_entities": graph.num_entities,
        "num_edges": graph.num_edges,
        "metrics": [{"name": m.name, "value": round(m.value, 4)} for m in metrics],
        "components": components,
        "edges": edges,
        "failures": failures,
        "traces": _derive_traces(names, edges),
    }


# ---------------------------------------------------------------------------
# Template. Single file, no external resources. __DATA__ is the model JSON.
# ---------------------------------------------------------------------------

_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Architecture Recovery · __REPO__</title>
<style>
:root{
  --bg:#0b0e14;--bg2:#0f131c;--panel:#141a25;--panel2:#1b2333;
  --line2:#2a3448;--fg:#dbe4f0;--muted:#7d8aa0;
  --blue:#3b82f6;--purple:#8b5cf6;--cyan:#22d3ee;--green:#22c55e;--amber:#f59e0b;
  --red:#ef4444;--orange:#fb923c;
}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Inter,sans-serif;
  background:var(--bg);color:var(--fg);font-size:14px;display:flex;flex-direction:column;overflow:hidden}
#app{flex:1;display:flex;min-height:0}

/* ---------- sidebar ---------- */
#sidebar{width:230px;min-width:230px;background:var(--bg2);border-right:1px solid var(--line2);
  display:flex;flex-direction:column}
.brand{padding:16px 18px 14px;border-bottom:1px solid var(--line2)}
.brand h1{margin:0;font-size:14px;font-weight:700}
.brand .sub{color:var(--muted);font-size:11.5px;margin-top:2px}
nav{padding:10px 8px;display:flex;flex-direction:column;gap:2px}
nav button{display:flex;align-items:center;gap:9px;width:100%;padding:8px 10px;border:0;
  background:none;color:var(--muted);font:inherit;font-size:13px;border-radius:8px;cursor:pointer;text-align:left}
nav button:hover{background:var(--panel);color:var(--fg)}
nav button.active{background:#1d2c4a;color:#c7d7f5}
nav button .ic{width:16px;text-align:center;opacity:.85}
nav button .badge{margin-left:auto;background:var(--blue);color:#fff;font-size:10.5px;
  min-width:17px;height:17px;border-radius:9px;display:inline-flex;align-items:center;justify-content:center;padding:0 5px}
nav button .badge.warn{background:var(--red)}
nav button .badge.dim{background:#33405c}

/* ---------- main ---------- */
#main{flex:1;display:flex;flex-direction:column;min-width:0}
#topbar{display:flex;align-items:center;gap:12px;padding:12px 20px;border-bottom:1px solid var(--line2);
  background:var(--bg2);min-height:52px}
#topbar h2{margin:0;font-size:15px;font-weight:650}
#topbar .spacer{flex:1}
.seg{display:flex;border:1px solid var(--line2);border-radius:8px;overflow:hidden}
.seg button{border:0;background:none;color:var(--muted);padding:5px 12px;font:inherit;font-size:12px;cursor:pointer}
.seg button.active{background:var(--panel2);color:var(--fg)}
select,.btn{background:var(--panel);border:1px solid var(--line2);color:var(--fg);border-radius:8px;
  padding:5px 10px;font:inherit;font-size:12.5px;cursor:pointer}
.btn.primary{background:#173764;border-color:#2b5aa0;color:#cfe1ff}
.btn.accent{background:#2c1e5c;border-color:#5b3fb8;color:#ddd0ff}
.btn.danger{color:#ff9b9b;border-color:#6b2d2d}
.btn.green{background:#123a24;border-color:#1f7a45;color:#b9f4d0}
.btn:disabled{opacity:.45;cursor:default}
#views{flex:1;position:relative;min-height:0}
.view{position:absolute;inset:0;display:none;overflow:auto}
.view.active{display:flex;flex-direction:column}

/* ---------- graph ---------- */
.graphwrap{flex:1;position:relative;overflow:hidden;min-height:0}
.graphwrap svg{width:100%;height:100%;display:block;cursor:grab}
.graphwrap svg.panning{cursor:grabbing}
.gtools{position:absolute;left:14px;bottom:14px;display:flex;gap:6px}
.gtools button{width:30px;height:30px;border-radius:8px;border:1px solid var(--line2);
  background:var(--panel);color:var(--muted);cursor:pointer;font-size:14px}
.gtools button:hover{color:var(--fg)}
.edge{fill:none;stroke:#33415e;stroke-width:1.4}
.edge.cyclic{stroke:#7a3a46}
.edge.dim{opacity:.12}
.edge.lit{stroke:var(--cyan);stroke-width:2.2;filter:drop-shadow(0 0 4px #22d3ee88)}
.edge.dashed{stroke-dasharray:5 5}
.elabel{font-size:9.5px;fill:#5f7194}
.elabel.dim{opacity:.12}
.node-fo{overflow:visible}
.node-card{width:100%;height:100%;background:#10151f;border:1.4px solid #33415e;border-radius:10px;
  padding:9px 11px;cursor:pointer;position:relative;transition:border-color .15s,opacity .2s}
.node-card:hover{border-color:var(--blue)}
.node-card.sel{border-color:var(--blue);box-shadow:0 0 0 1px var(--blue),0 0 14px #3b82f655}
.node-card.dim{opacity:.16}
.node-card.lit{border-color:var(--cyan);box-shadow:0 0 0 1px var(--cyan),0 0 16px #22d3ee55}
.node-card.fail{border-color:var(--orange);box-shadow:0 0 0 1px var(--orange),0 0 14px #fb923c44}
.node-card.done{border-color:var(--green)}
.nc-head{display:flex;align-items:center;gap:7px;min-width:0}
.nc-ic{width:18px;height:18px;border-radius:5px;display:flex;align-items:center;justify-content:center;
  font-size:10px;color:#fff;flex:none}
.nc-title{font-size:12px;font-weight:650;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.nc-tech{display:inline-block;margin-top:5px;font-size:9.5px;color:var(--muted);
  border:1px solid var(--line2);border-radius:4px;padding:1px 6px}
.nc-desc{margin-top:6px;font-size:10px;line-height:1.45;color:#8fa0ba;display:-webkit-box;
  -webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.nc-dot{position:absolute;top:7px;right:8px;width:7px;height:7px;border-radius:50%}
.nc-dot.high{background:var(--red)}.nc-dot.medium{background:var(--amber)}.nc-dot.low{background:#64748b}
.pulse{fill:var(--cyan);filter:drop-shadow(0 0 6px #22d3ee)}

/* ---------- detail panel ---------- */
#detail{position:absolute;top:0;right:0;bottom:0;width:330px;background:var(--panel);
  border-left:1px solid var(--line2);transform:translateX(100%);transition:transform .2s;overflow:auto;
  padding:16px;z-index:5}
#detail.open{transform:none}
#detail .close{position:absolute;top:10px;right:12px;background:none;border:0;color:var(--muted);
  font-size:16px;cursor:pointer}
#detail h3{margin:2px 0 2px;font-size:15px;color:#9ec1ff}
#detail .meta{color:var(--muted);font-size:12px;margin-bottom:10px}
.kv{display:flex;gap:6px;flex-wrap:wrap;margin:8px 0}
.pill{background:var(--panel2);border:1px solid var(--line2);border-radius:6px;padding:2px 8px;font-size:11px}
.sec{margin-top:14px}
.sec b{font-size:10.5px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);display:block;margin-bottom:6px}
.chip{display:inline-block;background:var(--panel2);border:1px solid #2b4a6b;border-radius:999px;
  padding:2px 10px;font-size:11px;color:#8fb8f2;cursor:pointer;margin:0 4px 4px 0}
.chip:hover{border-color:var(--blue)}
.ent{display:inline-block;font-size:11px;color:var(--muted);margin:1px 8px 1px 0}
.ent .k{color:#6ea8e8;opacity:.8}

/* ---------- cards / lists ---------- */
.pad{padding:18px 20px}
.cardgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px}
.fcard{background:var(--panel);border:1px solid var(--line2);border-radius:10px;padding:12px 14px;font-size:12px}
.fcard .fhead{display:flex;align-items:center;gap:8px;margin-bottom:4px}
.fcard .fname{font-weight:650;font-size:12.5px}
.sev{margin-left:auto;font-size:9.5px;font-weight:700;text-transform:uppercase;border-radius:5px;padding:2px 7px}
.sev.high{background:#dc262622;color:#ff8f8f;border:1px solid #dc262666}
.sev.critical{background:#dc2626;color:#fff}
.sev.medium{background:#f59e0b22;color:#fbc86a;border:1px solid #f59e0b55}
.sev.low{background:#33405c55;color:#9aa8c0;border:1px solid #33405c}
.fcard .fhl{color:var(--orange);font-weight:600;margin-bottom:5px}
.fcard p{margin:0 0 7px;color:#a9b6cb;line-height:1.5}
.fcard .mit{color:var(--muted)}
.fcard .mit em{color:#8fb8f2;font-style:normal;font-weight:600}
.fcard .mttr{margin-top:7px;color:var(--muted);font-size:11px}
.empty{color:var(--muted);padding:40px;text-align:center;width:100%}

/* ---------- dependencies ---------- */
.depcols{display:grid;grid-template-columns:minmax(280px,380px) 1fr;gap:16px;align-items:start}
@media(max-width:1000px){.depcols{grid-template-columns:1fr}}
.panelbox{background:var(--panel);border:1px solid var(--line2);border-radius:10px;padding:14px}
.panelbox h3{margin:0 0 10px;font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}
.deprow{display:flex;align-items:center;gap:8px;padding:5px 6px;border-radius:6px;font-size:12px}
.deprow:hover{background:var(--panel2)}
.deprow .arr{color:var(--muted)}
.deprow .w{margin-left:auto;color:#8fb8f2;font-size:11px}
.deprow.cyc .arr,.deprow.cyc .w{color:#ff8f8f}
.dsm{border-collapse:collapse;font-size:10.5px}
.dsm td,.dsm th{border:1px solid #222c40;text-align:center;min-width:30px;height:28px;padding:0 3px}
.dsm th{color:var(--muted);font-weight:600;background:var(--bg2)}
.dsm th.row{text-align:right;padding:0 8px;white-space:nowrap}
.dsm th.col div{writing-mode:vertical-rl;transform:rotate(180deg);margin:4px auto;white-space:nowrap;max-height:110px;overflow:hidden}
.dsm td.diag{background:#1a2232}
.dsm td.f{color:#fff;font-weight:600}

/* ---------- simulate ---------- */
#simctl{display:flex;align-items:center;gap:10px;padding:10px 20px;border-bottom:1px solid var(--line2);background:var(--bg2)}
#simctl .rbtn{width:30px;height:30px;border-radius:8px;border:1px solid var(--line2);background:var(--panel);
  color:var(--fg);cursor:pointer;font-size:12px}
#simctl input[type=range]{width:110px;accent-color:var(--blue)}
#simstatus{margin-left:auto;color:var(--muted);font-size:12px}
#simstatus .ready{color:var(--green)}
#simdesc{color:var(--muted);font-size:12px}
#waterfall{border-top:1px solid var(--line2);background:var(--bg2);padding:10px 20px;min-height:74px}
#wfrow{display:flex;gap:6px;overflow-x:auto;padding-bottom:4px}
.wf{border:1px solid var(--line2);border-radius:7px;padding:5px 9px;font-size:10.5px;min-width:86px;
  background:var(--panel);opacity:.3;flex:none}
.wf.on{opacity:1;border-color:#1f7a45}
.wf .nm{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:130px;color:#c6d2e4}
.wf .ms{color:#8fb8f2;margin-top:2px}
.wf .ok{display:inline-block;margin-top:3px;background:#123a24;color:#7fe0a4;border-radius:3px;
  font-size:9px;padding:0 5px;text-transform:uppercase}
#wftotal{margin-top:6px;font-size:12px;color:var(--muted)}
#wftotal b{color:var(--cyan)}
#tracebuild{display:none;align-items:center;gap:8px;padding:8px 20px;background:#241a3d;
  border-bottom:1px solid #4b3591;font-size:12px;color:#cfc2f5}
#tracebuild.on{display:flex}
#tracebuild .path{color:#fff;font-weight:600}

/* ---------- knowledge ---------- */
.kgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:12px;margin-bottom:18px}
.kcard{background:var(--panel);border:1px solid var(--line2);border-radius:10px;padding:13px 15px}
.kcard .kname{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted)}
.kcard .kval{font-size:22px;font-weight:700;margin:4px 0 5px;color:#9ec1ff}
.kcard p{margin:0;font-size:11.5px;color:#a9b6cb;line-height:1.5}

/* ---------- comments ---------- */
.comment{background:var(--panel);border:1px solid var(--line2);border-radius:10px;padding:10px 14px;
  margin-bottom:8px;font-size:13px;display:flex;gap:10px;align-items:baseline}
.comment .when{color:var(--muted);font-size:11px;flex:none}
.comment .del{margin-left:auto;background:none;border:0;color:var(--muted);cursor:pointer;font-size:13px}
.comment .del:hover{color:var(--red)}

/* ---------- feedback bar ---------- */
#feedbar{display:flex;align-items:center;gap:10px;padding:9px 16px;border-top:1px solid #4c2e8f;
  background:var(--bg2)}
#feedbar .fico{width:26px;height:26px;border-radius:50%;background:var(--panel2);display:flex;
  align-items:center;justify-content:center;font-size:12px;flex:none}
#feedbar .cnt{background:var(--purple);color:#fff;border-radius:9px;font-size:10.5px;min-width:17px;height:17px;
  display:inline-flex;align-items:center;justify-content:center;padding:0 5px;flex:none}
#feedbar input{flex:1;background:var(--panel);border:1px solid var(--line2);border-radius:8px;
  color:var(--fg);padding:7px 12px;font:inherit;font-size:13px;outline:none}
#feedbar input:focus{border-color:var(--purple)}
#toast{position:fixed;bottom:70px;left:50%;transform:translateX(-50%);background:#123a24;
  border:1px solid #1f7a45;color:#b9f4d0;border-radius:8px;padding:8px 16px;font-size:13px;
  opacity:0;transition:opacity .25s;pointer-events:none;z-index:99}
#toast.show{opacity:1}
</style></head><body>
<div id="app">
  <aside id="sidebar">
    <div class="brand"><h1>Architecture Recovery</h1><div class="sub" id="brandSub"></div></div>
    <nav id="nav"></nav>
  </aside>
  <div id="main">
    <div id="topbar">
      <h2 id="viewTitle"></h2><div class="spacer"></div>
      <div class="seg" id="levelSeg" style="display:none">
        <button data-lv="1" class="active">Components (L1)</button>
        <button data-lv="2">Detailed (L2)</button>
      </div>
      <select id="failSort" style="display:none">
        <option value="sev">Sort by Severity</option>
        <option value="comp">Sort by Component</option>
      </select>
      <select id="traceSel" style="display:none"></select>
      <button class="btn accent" id="newTraceBtn" style="display:none">+ New Trace</button>
      <button class="btn danger" id="delTraceBtn" style="display:none">Delete Trace</button>
    </div>
    <div id="views">
      <section class="view" id="view-arch">
        <div class="graphwrap" id="archwrap">
          <div class="gtools"><button id="afit" title="Fit">⤢</button><button id="azoomin">+</button><button id="azoomout">−</button></div>
        </div>
        <div id="detail"><button class="close" onclick="closeDetail()">✕</button><div id="detailBody"></div></div>
      </section>
      <section class="view" id="view-deps"><div class="pad depcols" id="depsBody"></div></section>
      <section class="view" id="view-fail"><div class="pad"><div class="cardgrid" id="failBody"></div></div></section>
      <section class="view" id="view-sim">
        <div id="simctl">
          <button class="rbtn" id="playBtn" title="Play">▶</button>
          <button class="rbtn" id="stopBtn" title="Stop">■</button>
          <span style="color:var(--muted);font-size:12px">Speed</span>
          <input type="range" id="speed" min="0.5" max="3" step="0.5" value="1">
          <span id="speedLabel" style="font-size:12px;color:var(--muted)">1x</span>
          <span id="simdesc"></span>
          <span id="simstatus"></span>
        </div>
        <div id="tracebuild">Recording trace — click components in order.
          <span class="path" id="draftPath"></span>
          <button class="btn green" id="saveTraceBtn">Save</button>
          <button class="btn" id="cancelTraceBtn">Cancel</button>
        </div>
        <div class="graphwrap" id="simwrap">
          <div class="gtools"><button id="sfit" title="Fit">⤢</button><button id="szoomin">+</button><button id="szoomout">−</button></div>
        </div>
        <div id="waterfall"><div id="wfrow"></div><div id="wftotal"></div></div>
      </section>
      <section class="view" id="view-know"><div class="pad" id="knowBody"></div></section>
      <section class="view" id="view-comm"><div class="pad" id="commBody" style="max-width:760px"></div></section>
    </div>
  </div>
</div>
<div id="feedbar">
  <div class="fico">💬</div><span class="cnt" id="fbCount">0</span>
  <span style="font-size:11px;color:var(--muted)">feedback</span>
  <input id="fbInput" placeholder='Type feedback for Claude (e.g., "split the Clustering component out of Recovery")…'>
  <button class="btn accent" id="fbAdd">+ Add</button>
  <button class="btn green" id="fbCopy">✓ Copy for Claude</button>
</div>
<div id="toast"></div>
<script>
const DATA = __DATA__;
const byName = Object.fromEntries(DATA.components.map(c=>[c.name,c]));
const LS = 'arcade-viz:'+DATA.repo;
const esc = s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const load = (k,d)=>{try{return JSON.parse(localStorage.getItem(LS+':'+k))??d}catch(e){return d}};
const save = (k,v)=>localStorage.setItem(LS+':'+k, JSON.stringify(v));
function toast(msg){const t=document.getElementById('toast');t.textContent=msg;t.classList.add('show');
  clearTimeout(t._h);t._h=setTimeout(()=>t.classList.remove('show'),2200);}

document.getElementById('brandSub').textContent = DATA.repo +
  (DATA.version && DATA.version!=='None' ? ' · '+String(DATA.version).slice(0,10) : '');

/* ============================== layout ============================== */
const ICON_COLORS=['#3b82f6','#8b5cf6','#f59e0b','#22c55e','#ec4899','#14b8a6','#f97316','#6366f1','#eab308','#06b6d4'];
const iconColor = n=>{let h=0;for(const ch of n)h=(h*31+ch.charCodeAt(0))>>>0;return ICON_COLORS[h%ICON_COLORS.length];};
const CARD_W={1:186,2:196}, CARD_H={1:74,2:132}, GAP_X=54, GAP_Y=88;

function computeLayout(level){
  const names=DATA.components.map(c=>c.name);
  const adj={},indexOf={};names.forEach((n,i)=>{adj[n]=[];indexOf[n]=i;});
  DATA.edges.forEach(e=>{if(adj[e.source]&&byName[e.target])adj[e.source].push(e.target);});
  // Break cycles with a DFS (ignore back edges), then layer = longest path depth.
  const state={},layer={};names.forEach(n=>layer[n]=0);
  const acyc={};names.forEach(n=>acyc[n]=[]);
  function dfs(n){state[n]=1;for(const t of adj[n]){if(state[t]===1)continue;acyc[n].push(t);if(!state[t])dfs(t);}state[n]=2;}
  names.forEach(n=>{if(!state[n])dfs(n);});
  const indeg={};names.forEach(n=>indeg[n]=0);
  names.forEach(n=>acyc[n].forEach(t=>indeg[t]++));
  const q=names.filter(n=>!indeg[n]);
  while(q.length){const n=q.shift();for(const t of acyc[n]){layer[t]=Math.max(layer[t],layer[n]+1);if(!--indeg[t])q.push(t);}}
  const layers=[];names.forEach(n=>{(layers[layer[n]]??=[]).push(n);});
  // Barycenter ordering: two down-up sweeps.
  const preds={};names.forEach(n=>preds[n]=[]);
  names.forEach(n=>acyc[n].forEach(t=>preds[t].push(n)));
  const pos={};layers.forEach(L=>L.forEach((n,i)=>pos[n]=i));
  for(let s=0;s<2;s++){
    for(let li=1;li<layers.length;li++){
      layers[li].sort((a,b)=>{
        const ba=preds[a].length?preds[a].reduce((s,p)=>s+pos[p],0)/preds[a].length:pos[a];
        const bb=preds[b].length?preds[b].reduce((s,p)=>s+pos[p],0)/preds[b].length:pos[b];
        return ba-bb;});
      layers[li].forEach((n,i)=>pos[n]=i);
    }
  }
  const W=CARD_W[level],H=CARD_H[level],out={};
  layers.forEach((L,li)=>{L.forEach((n,i)=>{
    out[n]={x:(i-(L.length-1)/2)*(W+GAP_X), y:li*(H+GAP_Y), w:W, h:H};});});
  return out;
}

/* ============================== graph render ============================== */
function makeGraph(container, opts){
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
  svg.innerHTML='<defs>'+
   '<marker id="arr'+opts.id+'" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'+
   '<path d="M0,0 L10,5 L0,10 z" fill="#42517a"/></marker>'+
   '<marker id="arrlit'+opts.id+'" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'+
   '<path d="M0,0 L10,5 L0,10 z" fill="#22d3ee"/></marker></defs>';
  const vp=document.createElementNS('http://www.w3.org/2000/svg','g');
  svg.appendChild(vp);container.appendChild(svg);
  const g={svg,vp,tf:{x:0,y:0,k:1},nodes:{},edges:{},level:1};

  function apply(){vp.setAttribute('transform',`translate(${g.tf.x},${g.tf.y}) scale(${g.tf.k})`);}
  svg.addEventListener('mousedown',ev=>{if(ev.target.closest('.node-card'))return;
    const s={x:ev.clientX,y:ev.clientY,tx:g.tf.x,ty:g.tf.y};svg.classList.add('panning');
    const mv=e=>{g.tf.x=s.tx+e.clientX-s.x;g.tf.y=s.ty+e.clientY-s.y;apply();};
    const up=()=>{svg.classList.remove('panning');removeEventListener('mousemove',mv);removeEventListener('mouseup',up);};
    addEventListener('mousemove',mv);addEventListener('mouseup',up);});
  svg.addEventListener('wheel',ev=>{ev.preventDefault();
    const r=svg.getBoundingClientRect(),mx=ev.clientX-r.left,my=ev.clientY-r.top;
    const k2=Math.min(2.5,Math.max(.2,g.tf.k*(ev.deltaY<0?1.12:0.89)));
    g.tf.x=mx-(mx-g.tf.x)*k2/g.tf.k;g.tf.y=my-(my-g.tf.y)*k2/g.tf.k;g.tf.k=k2;apply();},{passive:false});
  g.zoom=f=>{const r=svg.getBoundingClientRect(),mx=r.width/2,my=r.height/2;
    const k2=Math.min(2.5,Math.max(.2,g.tf.k*f));
    g.tf.x=mx-(mx-g.tf.x)*k2/g.tf.k;g.tf.y=my-(my-g.tf.y)*k2/g.tf.k;g.tf.k=k2;apply();};
  g.fit=()=>{const L=g.layout;if(!L)return;const xs=Object.values(L);
    const minX=Math.min(...xs.map(p=>p.x)),maxX=Math.max(...xs.map(p=>p.x+p.w));
    const minY=Math.min(...xs.map(p=>p.y)),maxY=Math.max(...xs.map(p=>p.y+p.h));
    const r=svg.getBoundingClientRect();
    const k=Math.min(1.15,(r.width-70)/(maxX-minX||1),(r.height-70)/(maxY-minY||1));
    g.tf.k=k;g.tf.x=(r.width-(maxX-minX)*k)/2-minX*k;g.tf.y=(r.height-(maxY-minY)*k)/2-minY*k;apply();};

  g.render=function(level){
    g.level=level;g.layout=computeLayout(level);vp.innerHTML='';g.nodes={};g.edges={};
    const L=g.layout;
    for(const e of DATA.edges){
      const s=L[e.source],t=L[e.target];if(!s||!t)continue;
      const p=document.createElementNS('http://www.w3.org/2000/svg','path');
      let d,lx,ly;
      const sx=s.x+s.w/2,tx=t.x+t.w/2;
      if(t.y>s.y){const sy=s.y+s.h,ty=t.y,m=(sy+ty)/2;
        d=`M${sx},${sy} C${sx},${m} ${tx},${m} ${tx},${ty-3}`;lx=(sx+tx)/2;ly=m;}
      else{const sy=s.y+s.h/2,ty=t.y+t.h/2;const side=sx<=tx?1:-1;
        const x1=s.x+(side>0?s.w:0),x2=t.x+(side>0?t.w:0);const off=60*side;
        d=`M${x1},${sy} C${x1+off},${sy} ${x2+off},${ty} ${x2+3*side},${ty}`;lx=(x1+x2)/2+off;ly=(sy+ty)/2;}
      p.setAttribute('d',d);p.setAttribute('class','edge'+(e.cyclic?' cyclic':''));
      p.setAttribute('marker-end',`url(#arr${opts.id})`);vp.appendChild(p);
      const tl=document.createElementNS('http://www.w3.org/2000/svg','text');
      tl.setAttribute('x',lx);tl.setAttribute('y',ly-4);tl.setAttribute('text-anchor','middle');
      tl.setAttribute('class','elabel');tl.textContent=e.weight+' ref'+(e.weight>1?'s':'');vp.appendChild(tl);
      g.edges[e.source+'→'+e.target]={path:p,label:tl,e};
    }
    for(const c of DATA.components){
      const pnt=L[c.name];if(!pnt)continue;
      const fo=document.createElementNS('http://www.w3.org/2000/svg','foreignObject');
      fo.setAttribute('x',pnt.x);fo.setAttribute('y',pnt.y);fo.setAttribute('width',pnt.w);
      fo.setAttribute('height',pnt.h);fo.setAttribute('class','node-fo');
      const sev=c.failure_ids.length?(DATA.failures[c.failure_ids[0]]||{}).severity:null;
      fo.innerHTML='<div class="node-card" xmlns="http://www.w3.org/1999/xhtml">'+
        (sev?`<span class="nc-dot ${sev}"></span>`:'')+
        `<div class="nc-head"><span class="nc-ic" style="background:${iconColor(c.name)}">${esc(c.name[0].toUpperCase())}</span>`+
        `<span class="nc-title">${esc(c.name)}</span></div>`+
        `<span class="nc-tech">${esc(c.kind)} · ${c.num_entities} entities</span>`+
        (level===2?`<div class="nc-desc">${esc(c.responsibility||'')}</div>`:'');
      const card=fo.firstChild;
      card.addEventListener('click',ev=>{ev.stopPropagation();opts.onNodeClick(c.name);});
      vp.appendChild(fo);g.nodes[c.name]={fo,card};
    }
    g.fit();
  };
  g.setNodeClass=(name,cls,on)=>{const n=g.nodes[name];if(n)n.card.classList.toggle(cls,on);};
  g.clearClasses=(...cls)=>{Object.values(g.nodes).forEach(n=>cls.forEach(c=>n.card.classList.remove(c)));
    Object.values(g.edges).forEach(x=>{cls.forEach(c=>{x.path.classList.remove(c);x.label.classList.remove(c);});
      x.path.setAttribute('marker-end',`url(#arr${opts.id})`);});};
  g.setEdgeClass=(s,t,cls,on)=>{const x=g.edges[s+'→'+t];if(!x)return;
    x.path.classList.toggle(cls,on);
    if(cls==='dim')x.label.classList.toggle('dim',on);
    if(cls==='lit')x.path.setAttribute('marker-end',on?`url(#arrlit${opts.id})`:`url(#arr${opts.id})`);};
  g.edgeEl=(s,t)=>g.edges[s+'→'+t];
  return g;
}

/* ============================== views / nav ============================== */
const customTraces=load('traces',[]);
const comments=load('comments',[]);
const VIEWS=[
  {id:'arch', ic:'▦', label:'Architecture', title:'Architecture'},
  {id:'deps', ic:'⑂', label:'Dependencies', title:'Dependencies'},
  {id:'fail', ic:'⚠', label:'Failure Points', title:()=>`Failure Points (${DATA.failures.length})`,
   badge:()=>DATA.failures.length, badgeCls:'warn'},
  {id:'sim',  ic:'▷', label:'Simulate', title:'Dependency Flow Simulation',
   badge:()=>DATA.traces.length+customTraces.length},
  {id:'know', ic:'▤', label:'Knowledge', title:'Knowledge', badge:()=>DATA.metrics.length,badgeCls:'dim'},
  {id:'comm', ic:'🗩', label:'Comments', title:'Comments', badge:()=>comments.length||null},
];
let current=null;
function renderNav(){
  document.getElementById('nav').innerHTML=VIEWS.map(v=>{
    const b=typeof v.badge==='function'?v.badge():null;
    return `<button data-v="${v.id}" class="${current===v.id?'active':''}"><span class="ic">${v.ic}</span>${v.label}`+
      (b?`<span class="badge ${v.badgeCls||''}">${b}</span>`:'')+'</button>';}).join('');
  document.querySelectorAll('#nav button').forEach(b=>b.onclick=()=>show(b.dataset.v));
}
function show(id){
  current=id;renderNav();
  const v=VIEWS.find(v=>v.id===id);
  document.getElementById('viewTitle').textContent=typeof v.title==='function'?v.title():v.title;
  document.querySelectorAll('.view').forEach(el=>el.classList.toggle('active',el.id==='view-'+id));
  document.getElementById('levelSeg').style.display=id==='arch'?'flex':'none';
  document.getElementById('failSort').style.display=id==='fail'?'':'none';
  ['traceSel','newTraceBtn','delTraceBtn'].forEach(x=>
    document.getElementById(x).style.display=id==='sim'?'':'none');
  if(id==='arch'&&!archG.layout)archG.render(level);
  if(id==='sim'&&!simG.layout){simG.render(1);selectTrace(traceSel.value);}
  if(id==='arch')requestAnimationFrame(()=>archG.fit());
  if(id==='sim')requestAnimationFrame(()=>simG.fit());
}

/* ---------- architecture view ---------- */
let level=1;
const archG=makeGraph(document.getElementById('archwrap'),{id:'A',onNodeClick:openDetail});
document.querySelectorAll('#levelSeg button').forEach(b=>b.onclick=()=>{
  level=+b.dataset.lv;
  document.querySelectorAll('#levelSeg button').forEach(x=>x.classList.toggle('active',x===b));
  archG.render(level);if(selected)mark(selected);});
document.getElementById('afit').onclick=()=>archG.fit();
document.getElementById('azoomin').onclick=()=>archG.zoom(1.25);
document.getElementById('azoomout').onclick=()=>archG.zoom(0.8);

let selected=null;
function mark(name){archG.clearClasses('sel');archG.setNodeClass(name,'sel',true);}
function openDetail(name){
  const c=byName[name];if(!c)return;selected=name;mark(name);
  const dep=(arr)=>arr.length?arr.map(d=>`<span class="chip" onclick="openDetail('${esc(d)}')">${esc(d)}</span>`).join(''):'<span class="meta">none</span>';
  const fails=c.failure_ids.map(i=>DATA.failures[i]).filter(Boolean);
  document.getElementById('detailBody').innerHTML=
    `<h3>${esc(c.name)}</h3><div class="meta">${esc(c.responsibility||'')}</div>`+
    `<div class="kv"><span class="pill">${c.num_entities} entities (${(c.share*100).toFixed(0)}%)</span>`+
    `<span class="pill">cohesion ${(+c.cohesion).toFixed(2)}</span>`+
    `<span class="pill">fan-in ${c.fan_in}</span><span class="pill">fan-out ${c.fan_out}</span></div>`+
    (fails.length?`<div class="sec"><b>Failure points</b>`+fails.map(f=>
      `<div class="fcard" style="margin-bottom:8px"><div class="fhead"><span class="fname">${esc(f.type)}</span>`+
      `<span class="sev ${f.severity}">${f.severity}</span></div>`+
      `<div class="fhl">${esc(f.headline)}</div><p>${esc(f.description)}</p></div>`).join('')+'</div>':'')+
    `<div class="sec"><b>Depends on (${c.depends_on.length})</b>${dep(c.depends_on)}</div>`+
    `<div class="sec"><b>Depended on by (${c.depended_on_by.length})</b>${dep(c.depended_on_by)}</div>`+
    `<div class="sec"><b>API surface (${c.api_surface.length})</b><div>`+
      (c.api_surface.length?c.api_surface.map(a=>`<span class="ent">${esc(a)}</span>`).join(''):'<span class="meta" style="color:var(--muted)">internal-only</span>')+'</div></div>'+
    `<div class="sec"><b>Entities</b><div>`+
      c.entities.map(e=>`<span class="ent"><span class="k">${esc(e.kind)}</span> ${esc(e.name)}</span>`).join('')+
      (c.entities_total>c.entities.length?`<span class="ent">+${c.entities_total-c.entities.length} more</span>`:'')+'</div></div>';
  document.getElementById('detail').classList.add('open');
}
function closeDetail(){document.getElementById('detail').classList.remove('open');
  selected=null;archG.clearClasses('sel');}
window.openDetail=openDetail;window.closeDetail=closeDetail;

/* ---------- dependencies view ---------- */
function renderDeps(){
  const rows=DATA.edges.map(e=>
    `<div class="deprow ${e.cyclic?'cyc':''}"><span>${esc(e.source)}</span><span class="arr">⟶${e.cyclic?'⟵':''}</span>`+
    `<span>${esc(e.target)}</span><span class="w">${e.weight} refs</span></div>`).join('');
  const names=DATA.components.slice().sort((a,b)=>b.num_entities-a.num_entities).map(c=>c.name);
  const W={};DATA.edges.forEach(e=>W[e.source+'→'+e.target]=e);
  const mx=Math.max(1,...DATA.edges.map(e=>e.weight));
  let dsm='<table class="dsm"><tr><th></th>'+names.map(n=>`<th class="col"><div>${esc(n)}</div></th>`).join('')+'</tr>';
  for(const r of names){dsm+=`<tr><th class="row">${esc(r)}</th>`;
    for(const c of names){
      if(r===c){dsm+='<td class="diag"></td>';continue;}
      const e=W[r+'→'+c];
      if(!e){dsm+='<td></td>';continue;}
      const a=(0.2+0.8*e.weight/mx).toFixed(2);
      const bg=e.cyclic?`rgba(220,38,38,${a})`:`rgba(59,130,246,${a})`;
      dsm+=`<td class="f" style="background:${bg}" title="${esc(r)} → ${esc(c)}: ${e.weight}">${e.weight}</td>`;}
    dsm+='</tr>';}
  dsm+='</table>';
  document.getElementById('depsBody').innerHTML=
    `<div class="panelbox"><h3>Weighted dependencies (${DATA.edges.length})</h3>${rows||'<div class="empty">No inter-component dependencies.</div>'}</div>`+
    `<div class="panelbox" style="overflow:auto"><h3>Design structure matrix — row depends on column, red = cyclic</h3>${dsm}</div>`;
}

/* ---------- failure points view ---------- */
const SEVRANK={high:0,critical:0,medium:1,low:2};
function renderFails(){
  const mode=document.getElementById('failSort').value;
  const fs=DATA.failures.slice().sort((a,b)=>mode==='comp'
    ?a.component.localeCompare(b.component)
    :(SEVRANK[a.severity]??3)-(SEVRANK[b.severity]??3));
  document.getElementById('failBody').innerHTML=fs.length?fs.map(f=>
    `<div class="fcard"><div class="fhead"><span class="fname">${esc(f.component)}</span>`+
    `<span class="sev ${f.severity}">${f.severity}</span></div>`+
    `<div class="fhl">${esc(f.headline)}</div>`+
    `<p>${esc(f.description||f.impact)}</p>`+
    `<p class="mit"><em>Impact:</em> ${esc(f.impact)}</p>`+
    `<p class="mit"><em>Mitigation:</em> ${esc(f.mitigation)}</p>`+
    (f.affected.length>1?`<p class="mit"><em>Affected:</em> ${f.affected.map(esc).join(', ')}</p>`:'')+
    `<div class="mttr">Est. effort: ${esc(f.effort)}</div></div>`).join('')
   :'<div class="empty">No failure points detected. 🎉</div>';
}
document.getElementById('failSort').onchange=renderFails;

/* ---------- simulate view ---------- */
const simG=makeGraph(document.getElementById('simwrap'),{id:'S',onNodeClick:simNodeClick});
document.getElementById('sfit').onclick=()=>simG.fit();
document.getElementById('szoomin').onclick=()=>simG.zoom(1.25);
document.getElementById('szoomout').onclick=()=>simG.zoom(0.8);
const traceSel=document.getElementById('traceSel');
const EDGEW={};DATA.edges.forEach(e=>EDGEW[e.source+'→'+e.target]=e.weight);
let sim={trace:null,playing:false,raf:null,speed:1};
let draft=null;

function allTraces(){return DATA.traces.concat(customTraces);}
function refreshTraceSel(keep){
  traceSel.innerHTML=allTraces().map(t=>`<option value="${esc(t.id)}">${esc(t.name)}</option>`).join('')||'<option value="">— no traces —</option>';
  if(keep&&allTraces().some(t=>t.id===keep))traceSel.value=keep;
  renderNav();
}
traceSel.onchange=()=>selectTrace(traceSel.value);
function selectTrace(id){
  stopSim();
  sim.trace=allTraces().find(t=>t.id===id)||null;
  document.getElementById('delTraceBtn').disabled=!(sim.trace&&customTraces.includes(sim.trace));
  document.getElementById('simdesc').textContent=sim.trace?sim.trace.description||'':'';
  setStatus(sim.trace?'<span class="ready">Ready</span>':'Select a trace');
  paintTrace(-1);
  buildWaterfall();
}
function setStatus(html){document.getElementById('simstatus').innerHTML=html;}
function hops(t){const h=[];for(let i=0;i+1<t.path.length;i++)
  h.push({from:t.path[i],to:t.path[i+1],w:EDGEW[t.path[i]+'→'+t.path[i+1]]||0});return h;}

function paintTrace(step){ // step = index of last completed hop; -1 = pre-run
  simG.clearClasses('dim','lit','done','fail','sel');
  if(!sim.trace)return;
  const inTrace=new Set(sim.trace.path);
  Object.keys(simG.nodes).forEach(n=>{if(!inTrace.has(n))simG.setNodeClass(n,'dim',true);});
  Object.values(simG.edges).forEach(x=>{
    if(!(inTrace.has(x.e.source)&&inTrace.has(x.e.target)))
      {x.path.classList.add('dim');x.label.classList.add('dim');}});
  const H=hops(sim.trace);
  for(let i=0;i<=step&&i<H.length;i++){
    simG.setEdgeClass(H[i].from,H[i].to,'lit',true);
    simG.setNodeClass(H[i].from,'done',true);simG.setNodeClass(H[i].to,'done',true);
  }
  // failure-affected components inside the trace glow orange
  sim.trace.path.forEach(n=>{const c=byName[n];
    if(c&&c.failure_ids.length&&step<0)simG.setNodeClass(n,'fail',true);});
}
function buildWaterfall(done=-1){
  const row=document.getElementById('wfrow');
  if(!sim.trace){row.innerHTML='';document.getElementById('wftotal').textContent='';return;}
  const H=hops(sim.trace);
  row.innerHTML=H.map((h,i)=>`<div class="wf ${i<=done?'on':''}"><div class="nm">${esc(h.from)} → ${esc(h.to)}</div>`+
    `<div class="ms">${h.w?h.w+' refs':'no direct edge'}</div><span class="ok">${i<=done?'ok':'…'}</span></div>`).join('');
  const total=H.reduce((s,h)=>s+h.w,0);
  document.getElementById('wftotal').innerHTML=
    `Coupling along trace: <b>${total} refs</b> · ${H.length} hops · ${sim.trace.path.length} components`;
}
function stopSim(){sim.playing=false;if(sim.raf)cancelAnimationFrame(sim.raf);
  const p=document.getElementById('simPulse');if(p)p.remove();
  if(sim.trace){paintTrace(-1);buildWaterfall();setStatus('<span class="ready">Ready</span>');}}
document.getElementById('stopBtn').onclick=stopSim;
document.getElementById('speed').oninput=e=>{sim.speed=+e.target.value;
  document.getElementById('speedLabel').textContent=sim.speed+'x';};
document.getElementById('playBtn').onclick=()=>{
  if(!sim.trace||sim.playing)return;
  sim.playing=true;setStatus('Running…');
  const H=hops(sim.trace);let i=0;
  paintTrace(-1);buildWaterfall(-1);
  const pulse=document.createElementNS('http://www.w3.org/2000/svg','circle');
  pulse.setAttribute('r',5);pulse.setAttribute('class','pulse');pulse.id='simPulse';
  simG.vp.appendChild(pulse);
  function runHop(){
    if(!sim.playing)return;
    if(i>=H.length){pulse.remove();paintTrace(H.length-1);buildWaterfall(H.length-1);
      const total=H.reduce((s,h)=>s+h.w,0);sim.playing=false;
      setStatus(`<span class="ready">Complete — ${total} refs over ${H.length} hops</span>`);return;}
    const h=H[i];const x=simG.edgeEl(h.from,h.to);
    const dur=(700+Math.min(900,h.w*24))/sim.speed;
    const t0=performance.now();
    simG.setNodeClass(h.from,'lit',true);
    function frame(now){
      if(!sim.playing)return;
      const p=Math.min(1,(now-t0)/dur);
      if(x){const len=x.path.getTotalLength(),pt=x.path.getPointAtLength(len*p);
        pulse.setAttribute('cx',pt.x);pulse.setAttribute('cy',pt.y);}
      else{const a=simG.layout[h.from],b=simG.layout[h.to];
        if(a&&b){pulse.setAttribute('cx',a.x+a.w/2+(b.x-a.x)*p);
          pulse.setAttribute('cy',a.y+a.h/2+(b.y-a.y)*p);}}
      if(p<1){sim.raf=requestAnimationFrame(frame);}
      else{paintTrace(i);buildWaterfall(i);i++;sim.raf=requestAnimationFrame(runHop);}
    }
    sim.raf=requestAnimationFrame(frame);
  }
  runHop();
};

/* custom traces */
document.getElementById('newTraceBtn').onclick=()=>{
  stopSim();draft={path:[]};
  document.getElementById('tracebuild').classList.add('on');
  document.getElementById('draftPath').textContent='(empty)';
  simG.clearClasses('dim','lit','done','fail','sel');
};
function simNodeClick(name){
  if(!draft)return;
  draft.path.push(name);
  simG.setNodeClass(name,'sel',true);
  document.getElementById('draftPath').textContent=draft.path.join(' → ');
}
document.getElementById('cancelTraceBtn').onclick=()=>{
  draft=null;document.getElementById('tracebuild').classList.remove('on');selectTrace(traceSel.value);};
document.getElementById('saveTraceBtn').onclick=()=>{
  if(!draft||draft.path.length<2){toast('Click at least two components first');return;}
  const name=prompt('Trace name:','Custom trace '+(customTraces.length+1));
  if(!name)return;
  const t={id:'custom-'+Date.now(),name,description:'User-recorded trace.',path:draft.path};
  customTraces.push(t);save('traces',customTraces);
  draft=null;document.getElementById('tracebuild').classList.remove('on');
  refreshTraceSel(t.id);selectTrace(t.id);toast('Trace saved');
};
document.getElementById('delTraceBtn').onclick=()=>{
  const i=customTraces.indexOf(sim.trace);
  if(i<0)return;
  customTraces.splice(i,1);save('traces',customTraces);
  refreshTraceSel();selectTrace(traceSel.value);toast('Trace deleted');
};

/* ---------- knowledge view ---------- */
const METRIC_HELP={
  RCI:v=>v>=.7?'High ratio of internal to external coupling — components are cohesive.'
      :v>=.4?'Moderate cohesion: a fair share of dependencies cross component boundaries.'
      :'Low cohesion: most coupling crosses component boundaries — boundaries may not match reality.',
  TurboMQ:v=>v>=.6?'Good modularization quality: dense inside components, sparse between.'
      :v>=.3?'Middling modularization quality — some components are weakly separated.'
      :'Poor modularization quality: the clustering barely beats a random partition.',
  BasicMQ:v=>'Basic modularization quality (intra vs inter-connectivity trade-off).',
  IntraConnectivity:v=>'Average edge density inside components (higher is more cohesive).',
  InterConnectivity:v=>'Average edge density between component pairs (lower is better separation).',
};
function metricHelp(name,v){
  for(const k in METRIC_HELP)if(name.toLowerCase()===k.toLowerCase())return METRIC_HELP[k](v);
  return 'Architecture quality signal computed by arcade-agent.';
}
function renderKnow(){
  const cards=DATA.metrics.map(m=>
    `<div class="kcard"><div class="kname">${esc(m.name)}</div><div class="kval">${(+m.value).toFixed(3)}</div>`+
    `<p>${esc(metricHelp(m.name,+m.value))}</p></div>`).join('');
  const glos={};DATA.failures.forEach(f=>glos[f.type]=f);
  const gcards=Object.values(glos).map(f=>
    `<div class="kcard"><div class="kname">${esc(f.type)}</div><p style="margin-top:6px">${esc(f.impact)}</p></div>`).join('');
  const largest=DATA.components.slice().sort((a,b)=>b.num_entities-a.num_entities).slice(0,6);
  document.getElementById('knowBody').innerHTML=
    `<div class="panelbox" style="margin-bottom:16px"><h3>Recovery run</h3>`+
    `<div class="kv"><span class="pill">repo: ${esc(DATA.repo)}</span><span class="pill">language: ${esc(DATA.language||'auto')}</span>`+
    `<span class="pill">algorithm: ${esc(DATA.algorithm)}</span><span class="pill">${DATA.num_entities} entities</span>`+
    `<span class="pill">${DATA.num_edges} edges</span><span class="pill">${DATA.components.length} components</span></div></div>`+
    `<h3 style="font-size:11px;text-transform:uppercase;color:var(--muted)">Metrics</h3><div class="kgrid">${cards}</div>`+
    (gcards?`<h3 style="font-size:11px;text-transform:uppercase;color:var(--muted)">Smell glossary (detected types)</h3><div class="kgrid">${gcards}</div>`:'')+
    `<h3 style="font-size:11px;text-transform:uppercase;color:var(--muted)">Largest components</h3>`+
    `<div class="panelbox">`+largest.map(c=>
      `<div class="deprow"><span>${esc(c.name)}</span><span class="w">${c.num_entities} entities · ${(c.share*100).toFixed(0)}%</span></div>`).join('')+'</div>';
}

/* ---------- comments / feedback ---------- */
function renderComments(){
  document.getElementById('fbCount').textContent=comments.length;
  document.getElementById('commBody').innerHTML=(comments.length?comments.map((c,i)=>
    `<div class="comment"><span class="when">${esc(c.when)}</span><span>${esc(c.text)}</span>`+
    `<button class="del" onclick="delComment(${i})">✕</button></div>`).join('')
    :'<div class="empty">No feedback yet. Use the bar below — notes are saved in this browser and can be copied out as a prompt for Claude.</div>');
  renderNav();
}
window.delComment=i=>{comments.splice(i,1);save('comments',comments);renderComments();};
function addFeedback(){
  const inp=document.getElementById('fbInput');
  const text=inp.value.trim();if(!text)return;
  comments.push({text,when:new Date().toLocaleString(),view:current});
  save('comments',comments);inp.value='';renderComments();toast('Feedback added');
}
document.getElementById('fbAdd').onclick=addFeedback;
document.getElementById('fbInput').addEventListener('keydown',e=>{if(e.key==='Enter')addFeedback();});
document.getElementById('fbCopy').onclick=()=>{
  if(!comments.length){toast('No feedback to copy yet');return;}
  const prompt=`I analyzed the architecture of ${DATA.repo} with arcade-analyze `+
    `(algorithm: ${DATA.algorithm}, ${DATA.components.length} components, `+
    `${DATA.failures.length} failure points). Please act on this feedback:\n\n`+
    comments.map((c,i)=>`${i+1}. ${c.text}`).join('\n');
  (navigator.clipboard?navigator.clipboard.writeText(prompt):Promise.reject())
    .then(()=>toast('Copied — paste it to Claude'))
    .catch(()=>{window.prompt('Copy this for Claude:',prompt);});
};

/* ---------- boot ---------- */
renderDeps();renderFails();renderKnow();renderComments();refreshTraceSel();
show('arch');
if(allTraces().length)traceSel.value=allTraces()[0].id;
addEventListener('resize',()=>{if(current==='arch')archG.fit();if(current==='sim')simG.fit();});
</script>
</body></html>
"""


def render_html(model: dict) -> str:
    data_json = json.dumps(model, default=str).replace("</", "<\\/")
    return (_TEMPLATE
            .replace("__REPO__", model["repo"])
            .replace("__DATA__", data_json))


def main() -> None:
    p = argparse.ArgumentParser(description="App-style architecture visualizer (dark SPA)")
    p.add_argument("source", nargs="?", default=None,
                   help="Local source directory OR a git URL")
    add_common_args(p)
    p.add_argument("--algorithm", "-a", default="pkg", help="Recovery algorithm (default: pkg)")
    p.add_argument("--num-clusters", "-n", type=int, default=None,
                   help="Target cluster count (for wca/acdc/arc/limbo)")
    p.add_argument("--source-root", default=None, help="Sub-path treated as source root")
    p.add_argument("--use-llm", action="store_true", help="LLM-powered smell analysis")
    p.add_argument("--from-model", default=None, metavar="MODEL_JSON",
                   help="Render a prebuilt model JSON instead of analyzing a source "
                        "(no arcade-agent needed; for demos/tests)")
    p.add_argument("--dump-model", default=None, metavar="MODEL_JSON",
                   help="Also write the computed model JSON next to the report")
    p.add_argument("--output", "-o", default=None,
                   help="Output HTML path. Default: ./arcade-report/<name>-app.html")
    p.add_argument("--no-open", action="store_true", help="Do not auto-open the report.")
    args = p.parse_args()

    if args.from_model:
        model = json.loads(Path(args.from_model).expanduser().read_text())
    else:
        if not args.source:
            p.error("either <source> or --from-model is required")
        bootstrap(args.arcade_home)
        print(f"Analyzing {args.source} ...", flush=True)
        bundle = recover_bundle(args.source, args.language, args.source_root,
                                algorithm=args.algorithm,
                                num_clusters=args.num_clusters, use_llm=args.use_llm)
        model = _build_model(bundle, args.algorithm)

    out = (Path(args.output).expanduser().resolve() if args.output
           else Path.cwd() / "arcade-report" / f"{model['repo']}-app.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(model))
    if args.dump_model:
        Path(args.dump_model).expanduser().write_text(json.dumps(model, indent=2, default=str))

    emit_summary({
        "command": "visualizer",
        "repo": model["repo"],
        "num_components": len(model["components"]),
        "num_entities": model.get("num_entities"),
        "num_failure_points": len(model["failures"]),
        "num_traces": len(model["traces"]),
        "traces": [t["name"] for t in model["traces"]],
        "report_html": str(out),
    })
    print(f"\nVisualizer app: {out}")
    if not args.no_open:
        open_in_browser(out)


if __name__ == "__main__":
    main()
