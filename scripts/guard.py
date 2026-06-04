#!/usr/bin/env python3
"""arcade-guard: an architecture guardrail for AI coding agents.

Keeps an emerging codebase aligned to an author-defined architecture contract
(``architecture.spec.json``) while it's being built. Designed to be called by an
AI agent in its loop (advisory) and at the commit/CI boundary (blocking).

Sub-commands:
  init       Scaffold an architecture.spec.json from a template.
  check      Conformance verdict (PASS/WARN/FAIL) + violations + fixes. Exit≠0 on
             FAIL with --fail-on error — use as a pre-commit / CI gate.
  propose    PROACTIVE: "I'm about to add X" → which component/layer it belongs in
             and what it may depend on, from the spec. Call BEFORE writing code.
  preview    Would a <from> → <to> dependency be allowed? Check before adding it.
  explain    Why a violation (by id) breaks the contract + the cheapest fix.
  remediate  The ranked set of fixes to restore conformance.

    <ARCADE_AGENT_HOME>/.venv/bin/python guard.py <cmd> <source> [opts]

Conformance is deterministic: it uses the spec's file→component globs and the
dependency edges, not the clustering recovery.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _common import add_common_args, bootstrap, emit_summary, recover_bundle, smell_name
import _spec as S

# ---- Spec templates for `init` ------------------------------------------------
TEMPLATES = {
    "hexagonal": {
        "intent": "Hexagonal/ports-and-adapters: domain core is pure; adapters depend inward.",
        "components": [
            {"name": "domain", "match": "**/domain/**", "layer": "domain"},
            {"name": "application", "match": "**/application/**", "layer": "application"},
            {"name": "adapters", "match": "**/adapters/**", "layer": "infrastructure"},
            {"name": "api", "match": "**/api/**", "layer": "presentation"},
        ],
        "layers": ["presentation", "application", "domain", "infrastructure"],
        "allow": [{"from": "presentation", "to": "application"},
                  {"from": "application", "to": "domain"},
                  {"from": "infrastructure", "to": "domain"}],
        "forbid": [{"from": "presentation", "to": "infrastructure",
                    "why": "UI must not reach adapters/persistence directly"}],
        "budgets": {"no_cycles": True, "max_new_smells": 0, "min_turbomq": 0.4,
                    "max_component_entities": 200, "max_fan_in": 8},
        "evolution": {"requires_review": True},
    },
    "layered": {
        "intent": "Classic layered: presentation → service → repository → model.",
        "components": [
            {"name": "presentation", "match": "**/{web,api,controller,ui}/**", "layer": "presentation"},
            {"name": "service", "match": "**/{service,application}/**", "layer": "application"},
            {"name": "repository", "match": "**/{repository,dao,store}/**", "layer": "infrastructure"},
            {"name": "model", "match": "**/{model,domain,entity}/**", "layer": "domain"},
        ],
        "layers": ["presentation", "application", "domain", "infrastructure"],
        "allow": [{"from": "presentation", "to": "application"},
                  {"from": "application", "to": "domain"},
                  {"from": "application", "to": "infrastructure"},
                  {"from": "infrastructure", "to": "domain"}],
        "forbid": [{"from": "presentation", "to": "infrastructure",
                    "why": "Presentation must go through the application layer"}],
        "budgets": {"no_cycles": True, "max_new_smells": 0, "min_turbomq": 0.4,
                    "max_component_entities": 200, "max_fan_in": 8},
        "evolution": {"requires_review": True},
    },
    "clean": {
        "intent": "Clean architecture: dependencies point inward toward entities.",
        "components": [
            {"name": "entities", "match": "**/entities/**", "layer": "domain"},
            {"name": "usecases", "match": "**/usecases/**", "layer": "application"},
            {"name": "interface-adapters", "match": "**/adapters/**", "layer": "presentation"},
            {"name": "frameworks", "match": "**/{frameworks,infra,external}/**", "layer": "infrastructure"},
        ],
        "layers": ["frameworks", "interface-adapters", "usecases", "entities"],
        "allow": [],
        "forbid": [{"from": "entities", "to": "frameworks",
                    "why": "Entities must not depend on outer layers"}],
        "budgets": {"no_cycles": True, "max_new_smells": 0, "min_turbomq": 0.4,
                    "max_component_entities": 200, "max_fan_in": 8},
        "evolution": {"requires_review": True},
    },
    "mvc": {
        "intent": "MVC: controllers orchestrate; views and models stay separate.",
        "components": [
            {"name": "controllers", "match": "**/controllers/**", "layer": "presentation"},
            {"name": "views", "match": "**/views/**", "layer": "presentation"},
            {"name": "models", "match": "**/models/**", "layer": "domain"},
        ],
        "layers": ["presentation", "domain"],
        "allow": [{"from": "presentation", "to": "domain"}],
        "forbid": [{"from": "domain", "to": "presentation",
                    "why": "Models must not depend on controllers/views"}],
        "budgets": {"no_cycles": True, "max_new_smells": 0, "max_component_entities": 200},
        "evolution": {"requires_review": True},
    },
}


def _find_spec(source: str, explicit: str | None) -> Path | None:
    if explicit:
        return Path(explicit).expanduser()
    for cand in [Path(source) / "architecture.spec.json", Path.cwd() / "architecture.spec.json"]:
        if cand.is_file():
            return cand
    return None


def _allowed_targets(spec, comp_name: str) -> list[str]:
    """Components/layers `comp_name` is allowed to depend on, per the spec."""
    layer_of = {c["name"]: c.get("layer") for c in spec["components"]}
    ly = layer_of.get(comp_name)
    out = []
    for r in spec.get("allow", []):
        if r.get("from") in (comp_name, ly):
            out.append(r.get("to"))
    return sorted(set(out))


# ---- sub-commands -------------------------------------------------------------

def cmd_init(args) -> int:
    tpl = TEMPLATES[args.template]
    out = Path(args.output or "architecture.spec.json").expanduser()
    if out.exists() and not args.force:
        sys.exit(f"[arcade-guard] {out} exists. Use --force to overwrite.")
    out.write_text(json.dumps(tpl, indent=2) + "\n")
    print(f"Wrote {args.template} architecture spec to {out}")
    print("Edit the `match` globs to fit your layout, then run: guard.py check <source>")
    emit_summary({"command": "guard:init", "template": args.template, "spec": str(out)})
    return 0


def _conformance(args):
    spec_path = _find_spec(args.source, args.spec)
    if not spec_path or not spec_path.is_file():
        sys.exit("[arcade-guard] No architecture.spec.json found. Run `guard.py init` first.")
    spec = S.load_spec(spec_path)
    bundle = recover_bundle(args.source, args.language, args.source_root, algorithm="pkg")
    graph = bundle["graph"]
    ent2comp = S.map_entities(graph, spec)
    cedges = S.component_edges(graph, ent2comp)

    baseline = None
    bpath = Path(args.source) / ".arcade" / "baseline.json"
    if bpath.is_file():
        try:
            baseline = json.loads(bpath.read_text())
        except Exception:
            baseline = None

    violations = S.check_conformance(spec, graph, ent2comp, cedges,
                                     smells=bundle["smells"], metrics=bundle["metrics"],
                                     baseline=baseline)
    return spec, spec_path, bundle, ent2comp, cedges, violations


def cmd_check(args) -> int:
    spec, spec_path, bundle, ent2comp, cedges, violations = _conformance(args)
    v = S.verdict(violations)
    errs = [x for x in violations if x["severity"] == "error"]
    warns = [x for x in violations if x["severity"] == "warn"]

    icon = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌"}[v]
    print(f"\n{icon} {v} — {bundle['repo'].name} vs {spec_path.name} "
          f"({len(errs)} error, {len(warns)} warn)")
    for x in errs + warns:
        tag = "ERROR" if x["severity"] == "error" else "warn "
        print(f"  [{tag}] {x['rule']}: {x['message']}")
        if x["fix"]:
            print(f"          fix: {x['fix']}")

    emit_summary({
        "command": "guard:check", "repo": bundle["repo"].name, "spec": str(spec_path),
        "verdict": v, "num_errors": len(errs), "num_warnings": len(warns),
        "violations": violations,
        "unmapped_entities": sum(1 for c in ent2comp.values() if c == "(unmapped)"),
    })
    if v == "FAIL" and args.fail_on == "error":
        return 1
    return 0


def cmd_propose(args) -> int:
    spec_path = _find_spec(args.source, args.spec)
    if not spec_path:
        sys.exit("[arcade-guard] No architecture.spec.json found. Run `guard.py init` first.")
    spec = S.load_spec(spec_path)
    intent = args.intent.lower()

    # Score each component by keyword overlap with the intent / its match / layer.
    def score(c):
        toks = f"{c['name']} {c.get('layer','')} {c.get('match','')}".lower()
        toks = toks.replace("/", " ").replace("*", " ").replace("**", " ")
        return sum(1 for w in set(toks.split()) if len(w) > 2 and w in intent)

    ranked = sorted(spec["components"], key=score, reverse=True)
    best = ranked[0] if ranked and score(ranked[0]) > 0 else None
    suggestion = best["name"] if best else "(no clear match — pick by layer)"
    allowed = _allowed_targets(spec, best["name"]) if best else []
    forbidden = [f"{r['from']}→{r['to']}" + (f" ({r['why']})" if r.get("why") else "")
                 for r in spec.get("forbid", [])
                 if best and r.get("from") in (best["name"], best.get("layer"))]

    print(f"\nIntent: {args.intent}")
    print(f"→ Place it in component: **{suggestion}**"
          + (f"  (layer: {best.get('layer')})" if best and best.get("layer") else ""))
    if best:
        print(f"  Path convention: {best.get('match','(define a match glob)')}")
    print(f"  May depend on: {', '.join(allowed) if allowed else '(no allow rules — see layers)'}")
    if forbidden:
        print(f"  Must NOT depend on: {', '.join(forbidden)}")

    emit_summary({
        "command": "guard:propose", "intent": args.intent,
        "suggested_component": suggestion,
        "suggested_layer": best.get("layer") if best else None,
        "path_convention": best.get("match") if best else None,
        "may_depend_on": allowed, "must_not_depend_on": forbidden,
    })
    return 0


def cmd_preview(args) -> int:
    spec_path = _find_spec(args.source, args.spec)
    if not spec_path:
        sys.exit("[arcade-guard] No architecture.spec.json found.")
    spec = S.load_spec(spec_path)
    layer_of = {c["name"]: c.get("layer") for c in spec["components"]}
    frm, to = args.frm, args.to
    problems = []
    for r in spec.get("forbid", []):
        if r.get("from") in (frm, layer_of.get(frm)) and r.get("to") in (to, layer_of.get(to)):
            problems.append(f"forbidden: {r['from']}→{r['to']}"
                            + (f" ({r['why']})" if r.get("why") else ""))
    lr = {ly: i for i, ly in enumerate(spec.get("layers", []))}
    ls, lt = layer_of.get(frm), layer_of.get(to)
    allowed_pairs = {(r.get("from"), r.get("to")) for r in spec.get("allow", [])}
    if ls in lr and lt in lr and lr[ls] > lr[lt] \
            and (ls, lt) not in allowed_pairs and (frm, to) not in allowed_pairs:
        problems.append(f"layer violation: {frm} ({ls}) → {to} ({lt}) points outward/upward")

    ok = not problems
    print(f"\n{'✅ ALLOWED' if ok else '❌ WOULD VIOLATE'}: {frm} → {to}")
    for p in problems:
        print(f"  - {p}")
    emit_summary({"command": "guard:preview", "from": frm, "to": to,
                  "allowed": ok, "problems": problems})
    return 0


def cmd_explain(args) -> int:
    spec, spec_path, bundle, ent2comp, cedges, violations = _conformance(args)
    match = next((v for v in violations if v["id"] == args.violation), None)
    if not match:
        print(f"No violation with id '{args.violation}'. Current ids: "
              + ", ".join(v["id"] for v in violations) or "(none)")
        return 0
    print(f"\n{match['id']} [{match['severity']}] {match['rule']}")
    print(f"  what: {match['message']}")
    print(f"  fix:  {match['fix']}")
    emit_summary({"command": "guard:explain", "violation": match})
    return 0


def cmd_remediate(args) -> int:
    spec, spec_path, bundle, ent2comp, cedges, violations = _conformance(args)
    order = {"error": 0, "warn": 1}
    ranked = sorted(violations, key=lambda v: order.get(v["severity"], 2))
    print(f"\nRemediation plan — {len(ranked)} item(s), errors first:")
    for i, v in enumerate(ranked, 1):
        print(f"  {i}. [{v['severity']}] {v['rule']}: {v['message']}\n     → {v['fix']}")
    if not ranked:
        print("  Nothing to fix — the codebase conforms to the contract. ✅")
    emit_summary({"command": "guard:remediate", "repo": bundle["repo"].name,
                  "actions": ranked})
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="Architecture guardrail for AI coding agents")
    sub = p.add_subparsers(dest="cmd", required=True)

    def with_source(sp):
        sp.add_argument("source", help="Local source directory OR a git URL")
        add_common_args(sp)
        sp.add_argument("--source-root", default=None, help="Sub-path treated as source root")
        sp.add_argument("--spec", default=None,
                        help="Path to architecture.spec.json (else auto-discovered)")

    sp = sub.add_parser("init", help="Scaffold an architecture.spec.json")
    sp.add_argument("--template", choices=list(TEMPLATES), default="hexagonal")
    sp.add_argument("--output", "-o", default=None)
    sp.add_argument("--force", action="store_true")
    sp.add_argument("--arcade-home", default=None)
    sp.set_defaults(func=cmd_init, needs_bootstrap=False)

    sp = sub.add_parser("check", help="Conformance verdict + violations (gate-able)")
    with_source(sp)
    sp.add_argument("--fail-on", choices=["error", "never"], default="error")
    sp.set_defaults(func=cmd_check, needs_bootstrap=True)

    sp = sub.add_parser("propose", help="Proactive: where should a new thing live?")
    with_source(sp)
    sp.add_argument("--intent", required=True, help="What you're about to add, in words")
    sp.set_defaults(func=cmd_propose, needs_bootstrap=False)

    sp = sub.add_parser("preview", help="Would a from→to dependency be allowed?")
    with_source(sp)
    sp.add_argument("--from", dest="frm", required=True, help="Source component")
    sp.add_argument("--to", dest="to", required=True, help="Target component")
    sp.set_defaults(func=cmd_preview, needs_bootstrap=False)

    sp = sub.add_parser("explain", help="Explain a violation by id")
    with_source(sp)
    sp.add_argument("--violation", required=True)
    sp.set_defaults(func=cmd_explain, needs_bootstrap=True)

    sp = sub.add_parser("remediate", help="Ranked fixes to restore conformance")
    with_source(sp)
    sp.set_defaults(func=cmd_remediate, needs_bootstrap=True)

    args = p.parse_args()
    if getattr(args, "needs_bootstrap", False):
        bootstrap(args.arcade_home)
    elif args.cmd in ("propose", "preview", "init"):
        # These don't parse code, but still need arcade-home resolution off for init;
        # propose/preview only read the spec.
        pass
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
