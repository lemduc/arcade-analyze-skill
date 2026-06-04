#!/usr/bin/env python3
"""arcade-analyze interactive: an explorable HTML architecture report.

Where analyze.py produces a static report (read top-to-bottom), this produces an
interactive one: click a component in the diagram (or the list) and a side panel
drills into it — its entities, what it depends on / what depends on it, cohesion,
its API surface, and the smells that touch it. Dependency chips are themselves
clickable, so you can walk the architecture instead of scrolling it.

    <ARCADE_AGENT_HOME>/.venv/bin/python interactive_report.py <source> \
        [--language java] [-o report.html]

All data is embedded in the single HTML file; only the Mermaid diagram library
loads from a CDN.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from _common import add_common_args, bootstrap, emit_summary, open_in_browser, recover_bundle, smell_name


def _node_id(name: str) -> str:
    nid = "".join(c if c.isalnum() or c == "_" else "_" for c in name)
    return nid or "c"


def _build_model(bundle) -> dict:
    """Assemble the per-component drill-down model the UI renders."""
    from arcade_agent.tools.explain_component import explain_component

    arch, graph, smells, metrics = (bundle["arch"], bundle["graph"],
                                    bundle["smells"], bundle["metrics"])
    total = graph.num_entities or 1

    # Map each component to the smells that affect it.
    smells_by_comp: dict[str, list] = {}
    smell_list = []
    for i, s in enumerate(smells):
        rec = {"id": i, "type": smell_name(s), "severity": s.severity,
               "description": s.description, "affected": s.affected_components or []}
        smell_list.append(rec)
        for c in (s.affected_components or []):
            smells_by_comp.setdefault(c, []).append(rec)

    components = []
    for c in arch.components:
        det = explain_component(arch, graph, c.name)
        if "error" in det:
            continue
        # Keep the entity list bounded so the page stays light on huge components.
        entities = det.get("entities", [])
        components.append({
            "name": c.name,
            "node_id": _node_id(c.name),
            "responsibility": det.get("responsibility", ""),
            "num_entities": det.get("num_entities", len(c.entities)),
            "share": round(det.get("num_entities", len(c.entities)) / total, 4),
            "cohesion": det.get("cohesion", 0),
            "depends_on": det.get("depends_on", []),
            "depended_on_by": det.get("depended_on_by", []),
            "api_surface": [f.rsplit(".", 1)[-1] for f in det.get("api_surface", [])][:40],
            "entities": [{"name": e["name"], "kind": e["kind"]} for e in entities][:60],
            "entities_total": len(entities),
            "smells": smells_by_comp.get(c.name, []),
        })

    # Mermaid diagram with click directives bound to selectComponent().
    deps = arch.component_dependencies(graph)
    lines = ["graph TD"]
    for c in components:
        lines.append(f'    {c["node_id"]}["{c["name"]}<br/>{c["num_entities"]}"]')
    for s, t in deps:
        lines.append(f"    {_node_id(s)} --> {_node_id(t)}")
    for c in components:
        lines.append(f'    click {c["node_id"]} selectComponent')

    return {
        "repo": bundle["repo"].name,
        "version": bundle["repo"].version,
        "num_entities": graph.num_entities,
        "num_edges": graph.num_edges,
        "num_components": len(components),
        "metrics": [{"name": m.name, "value": round(m.value, 4)} for m in metrics],
        "components": components,
        "smells": smell_list,
        "mermaid": "\n".join(lines),
    }


_TEMPLATE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>arcade-analyze · __REPO__</title>
<script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
<style>
 :root{--bg:#0f172a;--panel:#1e293b;--panel2:#273449;--line:#334155;--fg:#e2e8f0;
   --muted:#94a3b8;--accent:#38bdf8;--hi:#fbbf24;--red:#f87171;--amber:#fbbf24;--blue:#60a5fa}
 *{box-sizing:border-box} body{margin:0;font-family:-apple-system,BlinkMacSystemFont,
   'Segoe UI',Roboto,sans-serif;background:var(--bg);color:var(--fg);line-height:1.5}
 header{padding:1rem 1.5rem;border-bottom:1px solid var(--line);display:flex;
   align-items:baseline;gap:1rem;flex-wrap:wrap}
 header h1{font-size:1.15rem;margin:0} header .sub{color:var(--muted);font-size:.85rem}
 .stats{display:flex;gap:.6rem;margin-left:auto;flex-wrap:wrap}
 .stat{background:var(--panel);border:1px solid var(--line);border-radius:8px;
   padding:.35rem .7rem;font-size:.8rem} .stat b{color:var(--accent);font-size:1rem}
 .layout{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(320px,1fr);
   gap:1rem;padding:1rem 1.5rem;align-items:start}
 @media(max-width:900px){.layout{grid-template-columns:1fr}}
 .card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:1rem}
 .card h2{font-size:.8rem;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);
   margin:0 0 .75rem}
 #diagram{overflow:auto} #diagram .node{cursor:pointer}
 .complist{display:flex;flex-wrap:wrap;gap:.4rem;margin-top:.75rem}
 .chip{background:var(--panel2);border:1px solid var(--line);border-radius:999px;
   padding:.25rem .7rem;font-size:.8rem;cursor:pointer;color:var(--fg)}
 .chip:hover{border-color:var(--accent)} .chip.active{background:var(--accent);color:#072030;
   border-color:var(--accent);font-weight:600}
 .chip .sz{color:var(--muted);margin-left:.35rem} .chip.active .sz{color:#0a3b52}
 #detail .empty{color:var(--muted);font-size:.9rem}
 #detail h3{margin:.2rem 0 .1rem;font-size:1.1rem;color:var(--accent)}
 .meta{color:var(--muted);font-size:.82rem;margin-bottom:.6rem}
 .kv{display:flex;gap:.5rem;flex-wrap:wrap;margin:.4rem 0}
 .pill{background:var(--panel2);border:1px solid var(--line);border-radius:6px;
   padding:.15rem .5rem;font-size:.78rem}
 .sec{margin-top:.9rem} .sec b{font-size:.72rem;text-transform:uppercase;
   letter-spacing:.05em;color:var(--muted);display:block;margin-bottom:.35rem}
 .dep{cursor:pointer;color:var(--blue);border-color:#2b4a6b} .dep:hover{border-color:var(--blue)}
 .smell{border-left:3px solid var(--muted);padding:.3rem .6rem;margin:.3rem 0;
   background:var(--panel2);border-radius:0 6px 6px 0;font-size:.82rem}
 .smell.high{border-color:var(--red)} .smell.medium{border-color:var(--amber)}
 .sev{font-size:.68rem;text-transform:uppercase;font-weight:700;margin-right:.4rem}
 .sev.high{color:var(--red)} .sev.medium{color:var(--amber)} .sev.low{color:var(--muted)}
 .ent{display:inline-block;font-size:.75rem;color:var(--muted);margin:.1rem .35rem .1rem 0}
 .ent .k{color:var(--accent);opacity:.7}
 #smellsCard .smell{cursor:pointer} #smellsCard .smell:hover{background:var(--line)}
 code{background:var(--panel2);padding:.05rem .3rem;border-radius:4px}
</style></head><body>
<header>
 <h1>🏛 __REPO__ <span class="sub">__VERSION__</span></h1>
 <div class="stats" id="stats"></div>
</header>
<div class="layout">
 <div>
  <div class="card"><h2>Component map — click a node to inspect</h2>
   <div id="diagram"><pre class="mermaid">__MERMAID__</pre></div>
   <div class="complist" id="complist"></div>
  </div>
  <div class="card" id="smellsCard" style="margin-top:1rem"><h2>Architectural smells</h2>
   <div id="smells"></div></div>
 </div>
 <div class="card" id="detail"><h2>Component detail</h2>
  <div id="detailBody"><div class="empty">Select a component to drill in.</div></div>
 </div>
</div>
<script>
const DATA = __DATA__;
const byName = Object.fromEntries(DATA.components.map(c=>[c.name,c]));
const byNode = Object.fromEntries(DATA.components.map(c=>[c.node_id,c]));

// Stat cards
const m = Object.fromEntries(DATA.metrics.map(x=>[x.name,x.value]));
document.getElementById('stats').innerHTML = [
  ['Components',DATA.num_components],['Entities',DATA.num_entities],
  ['Edges',DATA.num_edges],['Smells',DATA.smells.length],
  ['RCI',(m.RCI??0).toFixed(2)],['TurboMQ',(m.TurboMQ??0).toFixed(2)]
].map(([l,v])=>`<div class="stat">${l} <b>${v}</b></div>`).join('');

// Component chips
document.getElementById('complist').innerHTML = DATA.components
  .slice().sort((a,b)=>b.num_entities-a.num_entities)
  .map(c=>`<span class="chip" data-c="${esc(c.name)}" onclick="pick('${esc(c.name)}')">`+
     `${esc(c.name)}<span class="sz">${c.num_entities}</span></span>`).join('');

// Smells list
document.getElementById('smells').innerHTML = DATA.smells.length ? DATA.smells.map(s=>
  `<div class="smell ${s.severity}" onclick="pick('${esc((s.affected[0]||''))}')">`+
  `<span class="sev ${s.severity}">${s.severity}</span><b>${esc(s.type)}</b> `+
  `${s.affected.length?'· '+s.affected.map(esc).join(', '):''}<br>`+
  `<span class="meta">${esc(s.description||'')}</span></div>`).join('')
  : '<div class="empty">No smells detected. 🎉</div>';

function esc(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}

function pick(name){
  const c = byName[name]; if(!c) return;
  document.querySelectorAll('.chip[data-c]').forEach(el=>
    el.classList.toggle('active', el.getAttribute('data-c')===name));
  document.querySelectorAll('#diagram .node').forEach(n=>n.style.outline='');
  const body = document.getElementById('detailBody');
  const dep = (arr,cls)=>arr.length? arr.map(d=>`<span class="pill ${cls}" `+
     (cls==='dep'?`onclick="pick('${esc(d)}')"`:'')+`>${esc(d)}</span>`).join(' ') : '<span class="meta">none</span>';
  const ents = c.entities.map(e=>`<span class="ent"><span class="k">${esc(e.kind)}</span> ${esc(e.name)}</span>`).join('')
     + (c.entities_total>c.entities.length?`<span class="meta"> +${c.entities_total-c.entities.length} more</span>`:'');
  body.innerHTML =
   `<h3>${esc(c.name)}</h3>`+
   `<div class="meta">${esc(c.responsibility||'')}</div>`+
   `<div class="kv"><span class="pill">${c.num_entities} entities (${(c.share*100).toFixed(0)}%)</span>`+
     `<span class="pill">cohesion ${(+c.cohesion).toFixed(2)}</span>`+
     `<span class="pill">depends on ${c.depends_on.length}</span>`+
     `<span class="pill">depended on by ${c.depended_on_by.length}</span></div>`+
   (c.smells.length?`<div class="sec"><b>Smells affecting this component</b>`+
     c.smells.map(s=>`<div class="smell ${s.severity}"><span class="sev ${s.severity}">${s.severity}</span>`+
       `<b>${esc(s.type)}</b><br><span class="meta">${esc(s.description||'')}</span></div>`).join('')+`</div>`:'')+
   `<div class="sec"><b>Depends on</b><div class="kv">${dep(c.depends_on,'dep')}</div></div>`+
   `<div class="sec"><b>Depended on by</b><div class="kv">${dep(c.depended_on_by,'dep')}</div></div>`+
   `<div class="sec"><b>API surface (${c.api_surface.length})</b><div>`+
     (c.api_surface.length?c.api_surface.map(a=>`<span class="ent">${esc(a)}</span>`).join(''):'<span class="meta">internal-only</span>')+`</div></div>`+
   `<div class="sec"><b>Entities</b><div>${ents}</div></div>`;
  const nodeEl = document.querySelector(`#diagram [id*="${c.node_id}"]`);
  if(nodeEl){const g=nodeEl.closest('.node')||nodeEl; g.style.outline='2px solid var(--hi)';
    g.scrollIntoView({block:'nearest',inline:'nearest'});}
}
// Mermaid click directive calls this with the node id.
function selectComponent(nodeId){const c=byNode[nodeId]; if(c) pick(c.name);}
window.selectComponent = selectComponent;

mermaid.initialize({startOnLoad:true, securityLevel:'loose', theme:'dark'});
</script>
</body></html>
"""


def render_html(model: dict) -> str:
    data_json = json.dumps(model, default=str).replace("</", "<\\/")
    return (_TEMPLATE
            .replace("__REPO__", model["repo"])
            .replace("__VERSION__", str(model["version"]))
            .replace("__MERMAID__", model["mermaid"])
            .replace("__DATA__", data_json))


def main() -> None:
    p = argparse.ArgumentParser(description="Interactive, explorable architecture HTML report")
    p.add_argument("source", help="Local source directory OR a git URL")
    add_common_args(p)
    p.add_argument("--algorithm", "-a", default="pkg", help="Recovery algorithm (default: pkg)")
    p.add_argument("--source-root", default=None, help="Sub-path treated as source root")
    p.add_argument("--use-llm", action="store_true", help="LLM-powered smell analysis")
    p.add_argument("--output", "-o", default=None,
                   help="Output HTML path. Default: ./arcade-report/<name>-interactive.html")
    p.add_argument("--no-open", action="store_true", help="Do not auto-open the report.")
    args = p.parse_args()

    bootstrap(args.arcade_home)
    print(f"Analyzing {args.source} ...", flush=True)
    bundle = recover_bundle(args.source, args.language, args.source_root,
                            algorithm=args.algorithm, use_llm=args.use_llm)
    model = _build_model(bundle)

    out = (Path(args.output).expanduser().resolve() if args.output
           else Path.cwd() / "arcade-report" / f"{bundle['repo'].name}-interactive.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(model))

    emit_summary({
        "command": "interactive",
        "repo": model["repo"],
        "num_components": model["num_components"],
        "num_entities": model["num_entities"],
        "num_smells": len(model["smells"]),
        "report_html": str(out),
    })
    print(f"\nInteractive report: {out}")
    if not args.no_open:
        open_in_browser(out)


if __name__ == "__main__":
    main()
