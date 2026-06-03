#!/usr/bin/env python3
"""arcade-analyze dsm: render a Design Structure Matrix (DSM) of the components.

For systems with more than ~10 components a Mermaid flowchart turns into
spaghetti. A DSM — a square matrix where row i, column j is the number of
dependencies from component i to component j — stays readable at 50+ components
and makes cycles and bottleneck columns pop out at a glance.

    <ARCADE_AGENT_HOME>/.venv/bin/python dsm.py <source> [--language java] \
        [-o dsm.html] [--order size|name]

Cells below the diagonal that mirror cells above it (i→j AND j→i) are
highlighted as cyclic. A dense column = a component everyone depends on.
"""

from __future__ import annotations

import argparse
import html as htmllib
from pathlib import Path

from _common import add_common_args, bootstrap, emit_summary, open_in_browser, recover_bundle


def _build_matrix(arch, graph):
    """Return (component_names, weight[i][j]) where weight = edges from i to j."""
    names = [c.name for c in arch.components]
    idx = {n: i for i, n in enumerate(names)}
    n = len(names)
    w = [[0] * n for _ in range(n)]
    for edge in graph.edges:
        sc = arch.component_of(edge.source)
        tc = arch.component_of(edge.target)
        if sc in idx and tc in idx and sc != tc:
            w[idx[sc]][idx[tc]] += 1
    return names, w


def _order(arch, names, w, mode):
    if mode == "size":
        size = {c.name: len(c.entities) for c in arch.components}
        order = sorted(range(len(names)), key=lambda i: -size[names[i]])
    else:
        order = sorted(range(len(names)), key=lambda i: names[i].lower())
    new_names = [names[i] for i in order]
    new_w = [[w[order[r]][order[c]] for c in range(len(names))] for r in range(len(names))]
    return new_names, new_w


def _render_html(repo_name, names, w) -> str:
    n = len(names)
    mx = max((w[i][j] for i in range(n) for j in range(n)), default=0) or 1
    cyclic = sum(1 for i in range(n) for j in range(i + 1, n) if w[i][j] and w[j][i])

    def cell(i, j):
        if i == j:
            return '<td class="diag"></td>'
        v = w[i][j]
        if not v:
            return '<td></td>'
        is_cycle = w[j][i] > 0
        alpha = 0.20 + 0.80 * (v / mx)
        bg = (f"rgba(220,38,38,{alpha:.2f})" if is_cycle
              else f"rgba(37,99,235,{alpha:.2f})")
        title = f"{names[i]} → {names[j]}: {v} dep(s)" + (" (cyclic)" if is_cycle else "")
        return f'<td class="f" style="background:{bg}" title="{htmllib.escape(title)}">{v}</td>'

    head = "".join(f'<th class="col"><div>{htmllib.escape(nm)}</div></th>' for nm in names)
    rows = []
    for i in range(n):
        cells = "".join(cell(i, j) for j in range(n))
        rows.append(f'<tr><th class="row">{htmllib.escape(names[i])}</th>{cells}</tr>')
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<title>DSM · {htmllib.escape(repo_name)}</title>
<style>
 body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:2rem;color:#1a1a2e}}
 h1{{font-size:1.3rem}} .sub{{color:#666;margin-bottom:1.2rem}}
 table{{border-collapse:collapse}} td,th{{border:1px solid #e2e8f0;text-align:center}}
 td{{width:34px;height:34px;font-size:.75rem;color:#fff;font-weight:600}}
 td.diag{{background:#1e293b}} td:not(.f){{color:#cbd5e1}}
 th.row{{text-align:right;padding:0 .5rem;font-size:.8rem;white-space:nowrap;background:#f8fafc}}
 th.col{{height:120px;vertical-align:bottom;background:#f8fafc}}
 th.col div{{writing-mode:vertical-rl;transform:rotate(180deg);font-size:.8rem;
   white-space:nowrap;margin:0 auto}}
 .legend{{margin-top:1rem;font-size:.85rem;color:#475569}}
 .swatch{{display:inline-block;width:12px;height:12px;border-radius:2px;vertical-align:middle}}
</style></head><body>
<h1>Design Structure Matrix — {htmllib.escape(repo_name)}</h1>
<div class="sub">{n} components · row → column means "row depends on column" ·
 {cyclic} cyclic pair(s) highlighted in red</div>
<table><tr><th></th>{head}</tr>{''.join(rows)}</table>
<div class="legend">
 <span class="swatch" style="background:rgba(37,99,235,.7)"></span> dependency &nbsp;
 <span class="swatch" style="background:rgba(220,38,38,.7)"></span> cyclic (both directions) &nbsp;
 <span class="swatch" style="background:#1e293b"></span> self (diagonal)
</div></body></html>"""


def main() -> None:
    p = argparse.ArgumentParser(description="Design Structure Matrix view of components")
    p.add_argument("source", help="Local source directory OR a git URL")
    add_common_args(p)
    p.add_argument("--algorithm", "-a", default="pkg", help="Recovery algorithm (default: pkg)")
    p.add_argument("--source-root", default=None, help="Sub-path treated as source root")
    p.add_argument("--order", choices=["size", "name"], default="size",
                   help="Row/column order (default: size, biggest first)")
    p.add_argument("--output", "-o", default=None,
                   help="Output HTML path. Default: ./arcade-report/<name>-dsm.html")
    p.add_argument("--no-open", action="store_true", help="Do not auto-open.")
    args = p.parse_args()

    bootstrap(args.arcade_home)
    print(f"Analyzing {args.source} ...", flush=True)
    bundle = recover_bundle(args.source, args.language, args.source_root, algorithm=args.algorithm)
    arch, graph = bundle["arch"], bundle["graph"]

    names, w = _build_matrix(arch, graph)
    names, w = _order(arch, names, w, args.order)
    n = len(names)
    cyclic_pairs = [(names[i], names[j]) for i in range(n) for j in range(i + 1, n)
                    if w[i][j] and w[j][i]]

    out = (Path(args.output).expanduser().resolve() if args.output
           else Path.cwd() / "arcade-report" / f"{bundle['repo'].name}-dsm.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_render_html(bundle["repo"].name, names, w))

    emit_summary({
        "command": "dsm",
        "repo": bundle["repo"].name,
        "num_components": n,
        "order": args.order,
        "components": names,
        "cyclic_pairs": [list(p) for p in cyclic_pairs],
        "report_html": str(out),
    })
    print(f"\nDSM written to: {out}")
    if not args.no_open:
        open_in_browser(out)


if __name__ == "__main__":
    main()
