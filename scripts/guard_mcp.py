#!/usr/bin/env python3
"""arcade-guard MCP server — the architecture guardrail as agent-callable tools.

Exposes the guardrail over the Model Context Protocol so any MCP agent (Claude
Code, Cursor, …) can consult it in its loop. Run it with arcade-agent's venv
interpreter and ARCADE_AGENT_HOME set:

    ARCADE_AGENT_HOME=/path/to/arcade-agent \
      /path/to/arcade-agent/.venv/bin/python <skill>/scripts/guard_mcp.py

Register in an MCP client, e.g. Claude Code (.mcp.json / settings):
    {"mcpServers": {"arcade-guard": {
       "command": "/path/to/arcade-agent/.venv/bin/python",
       "args": ["/path/to/arcade-analyze/scripts/guard_mcp.py"],
       "env": {"ARCADE_AGENT_HOME": "/path/to/arcade-agent"}}}}

Tools (all return compact JSON):
  check_architecture   conformance verdict + violations + fixes (advisory or gate)
  propose_placement    PROACTIVE: where a new thing should live + allowed deps
  preview_impact       would a from→to dependency be allowed?
  explain_violation    why a violation breaks the contract + the fix
  remediate            ranked fixes to restore conformance
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from _common import bootstrap, recover_bundle
import _spec as S

# Resolve arcade-agent and put it on sys.path up front (errors clearly if unset).
bootstrap(None)

from mcp.server.fastmcp import FastMCP  # noqa: E402

server = FastMCP(
    "arcade-guard",
    instructions=(
        "Architecture guardrail. The project's intended architecture lives in "
        "architecture.spec.json. BEFORE adding a module call propose_placement; "
        "BEFORE adding a cross-component dependency call preview_impact; AFTER a "
        "change call check_architecture and fix any ERROR. Conformance is "
        "deterministic. Keep `source` pointed at the project root."
    ),
)


def _find_spec(source: str, explicit: str | None) -> Path | None:
    if explicit:
        return Path(explicit).expanduser()
    for c in [Path(source) / "architecture.spec.json", Path.cwd() / "architecture.spec.json"]:
        if c.is_file():
            return c
    return None


def _run_check(source: str, language: str | None, source_root: str | None, spec: str | None):
    spec_path = _find_spec(source, spec)
    if not spec_path or not spec_path.is_file():
        return None, None, {"error": "No architecture.spec.json found. Create one (guard init)."}
    sp = S.load_spec(spec_path)
    bundle = recover_bundle(source, language, source_root, algorithm="pkg")
    graph = bundle["graph"]
    ent2comp = S.map_entities(graph, sp)
    cedges = S.component_edges(graph, ent2comp)
    baseline = None
    bp = Path(source) / ".arcade" / "baseline.json"
    if bp.is_file():
        try:
            baseline = json.loads(bp.read_text())
        except Exception:
            baseline = None
    violations = S.check_conformance(sp, graph, ent2comp, cedges,
                                     smells=bundle["smells"], metrics=bundle["metrics"],
                                     baseline=baseline)
    return sp, bundle, {"violations": violations, "ent2comp": ent2comp}


@server.tool()
def check_architecture(source: str = ".", language: str | None = None,
                       source_root: str | None = None, spec: str | None = None) -> str:
    """Check the codebase against its architecture contract. Returns the verdict
    (PASS/WARN/FAIL), each violation with a one-line fix, and counts. Call after a
    change; treat FAIL as must-fix before committing."""
    sp, bundle, res = _run_check(source, language, source_root, spec)
    if "error" in res:
        return json.dumps(res)
    v = res["violations"]
    out = {
        "verdict": S.verdict(v),
        "errors": sum(1 for x in v if x["severity"] == "error"),
        "warnings": sum(1 for x in v if x["severity"] == "warn"),
        "violations": [{"id": x["id"], "severity": x["severity"], "rule": x["rule"],
                        "message": x["message"], "fix": x["fix"]} for x in v[:40]],
    }
    return json.dumps(out)


@server.tool()
def propose_placement(intent: str, source: str = ".", spec: str | None = None) -> str:
    """PROACTIVE — call BEFORE writing new code. Given a description of what you're
    about to add, returns the component/layer it should live in, the path
    convention, and what it may (and must not) depend on, from the contract."""
    spec_path = _find_spec(source, spec)
    if not spec_path:
        return json.dumps({"error": "No architecture.spec.json found."})
    sp = S.load_spec(spec_path)
    il = intent.lower()

    def score(c):
        toks = f"{c['name']} {c.get('layer','')} {c.get('match','')}".lower()
        toks = toks.replace("/", " ").replace("*", " ")
        return sum(1 for w in set(toks.split()) if len(w) > 2 and w in il)

    ranked = sorted(sp["components"], key=score, reverse=True)
    best = ranked[0] if ranked and score(ranked[0]) > 0 else None
    layer_of = {c["name"]: c.get("layer") for c in sp["components"]}
    allowed = sorted({r["to"] for r in sp.get("allow", [])
                      if best and r.get("from") in (best["name"], best.get("layer"))})
    forbidden = [f"{r['from']}→{r['to']}" for r in sp.get("forbid", [])
                 if best and r.get("from") in (best["name"], best.get("layer"))]
    return json.dumps({
        "intent": intent,
        "suggested_component": best["name"] if best else None,
        "suggested_layer": best.get("layer") if best else None,
        "path_convention": best.get("match") if best else None,
        "may_depend_on": allowed,
        "must_not_depend_on": forbidden,
        "note": None if best else "No clear match — choose by layer from the spec.",
    })


@server.tool()
def preview_impact(from_component: str, to_component: str,
                   source: str = ".", spec: str | None = None) -> str:
    """Would a dependency from `from_component` to `to_component` be allowed by the
    contract? Call BEFORE adding a cross-component import."""
    spec_path = _find_spec(source, spec)
    if not spec_path:
        return json.dumps({"error": "No architecture.spec.json found."})
    sp = S.load_spec(spec_path)
    layer_of = {c["name"]: c.get("layer") for c in sp["components"]}
    problems = []
    for r in sp.get("forbid", []):
        if r.get("from") in (from_component, layer_of.get(from_component)) \
                and r.get("to") in (to_component, layer_of.get(to_component)):
            problems.append("forbidden: " + r["from"] + "→" + r["to"]
                            + (f" ({r['why']})" if r.get("why") else ""))
    lr = {ly: i for i, ly in enumerate(sp.get("layers", []))}
    ls, lt = layer_of.get(from_component), layer_of.get(to_component)
    allowed_pairs = {(r.get("from"), r.get("to")) for r in sp.get("allow", [])}
    if ls in lr and lt in lr and lr[ls] > lr[lt] \
            and (ls, lt) not in allowed_pairs and (from_component, to_component) not in allowed_pairs:
        problems.append(f"layer violation: {from_component} ({ls}) → {to_component} ({lt}) points outward")
    return json.dumps({"from": from_component, "to": to_component,
                       "allowed": not problems, "problems": problems})


@server.tool()
def explain_violation(violation_id: str, source: str = ".", language: str | None = None,
                      source_root: str | None = None, spec: str | None = None) -> str:
    """Explain why a specific violation (by id from check_architecture) breaks the
    contract, and the cheapest fix."""
    sp, bundle, res = _run_check(source, language, source_root, spec)
    if "error" in res:
        return json.dumps(res)
    match = next((x for x in res["violations"] if x["id"] == violation_id), None)
    if not match:
        return json.dumps({"error": f"No violation '{violation_id}'",
                           "available": [x["id"] for x in res["violations"]]})
    return json.dumps(match)


@server.tool()
def remediate(source: str = ".", language: str | None = None,
              source_root: str | None = None, spec: str | None = None) -> str:
    """Return the ranked set of fixes (errors first) to restore conformance."""
    sp, bundle, res = _run_check(source, language, source_root, spec)
    if "error" in res:
        return json.dumps(res)
    order = {"error": 0, "warn": 1}
    ranked = sorted(res["violations"], key=lambda v: order.get(v["severity"], 2))
    return json.dumps({"actions": [{"severity": v["severity"], "rule": v["rule"],
                                    "message": v["message"], "fix": v["fix"]}
                                   for v in ranked]})


def main() -> None:
    server.run()


if __name__ == "__main__":
    main()
