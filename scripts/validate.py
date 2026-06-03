#!/usr/bin/env python3
"""arcade-analyze validate: check a codebase against architecture rules.

Architects don't just observe architecture, they specify it. This reads a rules
file (.arcade-rules.json — or .yml/.yaml if PyYAML is installed) and checks the
recovered architecture for conformance, plus a heuristic layered/clean
architecture check. Exits non-zero when any rule is violated, so it works as a
CI gate (see assets/arch-gate.yml).

    <ARCADE_AGENT_HOME>/.venv/bin/python validate.py <source> \
        [--rules .arcade-rules.json] [--language java] [--fail-on error|never]

Rule types (see assets/arcade-rules.sample.json):
  forbidden-dependency  {from, to}            glob component names; flag from→to deps
  no-cycles             {}                     fail if any dependency-cycle smell
  metric-gate           {metric, min?, max?}   bound a metric (RCI, TurboMQ, ...)
  max-fan-in            {max}                   no component depended on by > max others
  max-component-size    {max}                   no component with > max entities
"""

from __future__ import annotations

import argparse
import json
import sys
from fnmatch import fnmatch
from pathlib import Path

from _common import add_common_args, bootstrap, emit_summary, recover_bundle, smell_name

# Heuristic layer keywords, outermost → innermost. A classic layered/clean
# architecture only allows dependencies pointing inward (rank increases).
LAYERS = [
    ("presentation", ["presentation", "ui", "view", "web", "controller", "rest",
                      "api", "handler", "gateway", "endpoint"]),
    ("application", ["application", "service", "usecase", "use_case", "facade",
                    "orchestrat", "workflow"]),
    ("domain", ["domain", "model", "entity", "core", "business"]),
    ("infrastructure", ["infrastructure", "infra", "persistence", "repository",
                       "repo", "dao", "data", "db", "adapter", "client", "io",
                       "storage", "external"]),
]


def _load_rules(path: Path) -> dict:
    text = path.read_text()
    if path.suffix in (".yml", ".yaml"):
        try:
            import yaml
        except ImportError:
            sys.exit(f"[arcade-analyze] {path} is YAML but PyYAML isn't installed. "
                     "Use a .json rules file, or `pip install pyyaml` in the arcade-agent venv.")
        return yaml.safe_load(text)
    return json.loads(text)


def _metric(metrics, name):
    for m in metrics:
        if m.name == name:
            return m.value
    return None


def _fan_in(arch, graph) -> dict[str, int]:
    counts = {c.name: 0 for c in arch.components}
    for src, tgt in arch.component_dependencies(graph):
        counts[tgt] = counts.get(tgt, 0) + 1
    return counts


def _check_rules(rules, arch, graph, metrics, smells) -> list[dict]:
    violations = []
    comp_deps = arch.component_dependencies(graph)
    fan_in = _fan_in(arch, graph)
    sizes = {c.name: len(c.entities) for c in arch.components}

    def add(rule, msg):
        violations.append({"rule": rule.get("name", rule.get("type")),
                           "type": rule["type"], "message": msg})

    for rule in rules.get("rules", []):
        rtype = rule.get("type")
        if rtype == "forbidden-dependency":
            frm, to = rule.get("from", "*"), rule.get("to", "*")
            hits = [f"{s} → {t}" for s, t in comp_deps if fnmatch(s, frm) and fnmatch(t, to)]
            for h in hits:
                add(rule, f"forbidden dependency: {h}")
        elif rtype == "no-cycles":
            for sm in smells:
                if smell_name(sm) == "Dependency Cycle":
                    add(rule, f"dependency cycle: {', '.join(sm.affected_components)}")
        elif rtype == "metric-gate":
            name = rule.get("metric")
            val = _metric(metrics, name)
            if val is None:
                add(rule, f"metric '{name}' not found")
            else:
                if "min" in rule and val < rule["min"]:
                    add(rule, f"{name}={val:.2f} below min {rule['min']}")
                if "max" in rule and val > rule["max"]:
                    add(rule, f"{name}={val:.2f} above max {rule['max']}")
        elif rtype == "max-fan-in":
            mx = rule.get("max", 0)
            for name, fi in fan_in.items():
                if fi > mx:
                    add(rule, f"{name} fan-in {fi} > {mx}")
        elif rtype == "max-component-size":
            mx = rule.get("max", 0)
            for name, sz in sizes.items():
                if sz > mx:
                    add(rule, f"{name} has {sz} entities > {mx}")
        else:
            add(rule, f"unknown rule type '{rtype}'")
    return violations


def _layer_of(name: str) -> tuple[int, str] | None:
    low = name.lower()
    for rank, (layer, kws) in enumerate(LAYERS):
        if any(kw in low for kw in kws):
            return rank, layer
    return None


def _detect_layers(arch, graph) -> dict:
    mapped = {}
    for c in arch.components:
        hit = _layer_of(c.name)
        if hit:
            mapped[c.name] = {"rank": hit[0], "layer": hit[1]}
    coverage = len(mapped) / max(1, len(arch.components))
    upward = []
    for s, t in arch.component_dependencies(graph):
        if s in mapped and t in mapped and mapped[s]["rank"] > mapped[t]["rank"]:
            upward.append(f"{s} ({mapped[s]['layer']}) → {t} ({mapped[t]['layer']})")
    detected = coverage >= 0.5
    return {
        "detected": detected,
        "coverage": round(coverage, 2),
        "layers": {n: m["layer"] for n, m in mapped.items()},
        "upward_violations": upward,
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Validate architecture against rules")
    p.add_argument("source", help="Local source directory OR a git URL")
    add_common_args(p)
    p.add_argument("--algorithm", "-a", default="pkg", help="Recovery algorithm (default: pkg)")
    p.add_argument("--source-root", default=None, help="Sub-path treated as source root")
    p.add_argument("--rules", default=None,
                   help="Rules file. Default: .arcade-rules.json in <source> then cwd.")
    p.add_argument("--fail-on", choices=["error", "never"], default="error",
                   help="error (default): exit 1 on any violation. never: always exit 0.")
    p.add_argument("--output", "-o", default=None, help="Write the markdown report here too")
    args = p.parse_args()

    bootstrap(args.arcade_home)

    # Locate the rules file.
    rules = {"rules": []}
    rules_path = None
    if args.rules:
        rules_path = Path(args.rules).expanduser()
    else:
        src = Path(args.source)
        for cand in [src / ".arcade-rules.json", Path.cwd() / ".arcade-rules.json"]:
            if cand.is_file():
                rules_path = cand
                break
    if rules_path and rules_path.is_file():
        rules = _load_rules(rules_path)
        print(f"Rules: {rules_path}", flush=True)
    else:
        print("No rules file found — running layered-architecture check only.", flush=True)

    print(f"Analyzing {args.source} ...", flush=True)
    bundle = recover_bundle(args.source, args.language, args.source_root, algorithm=args.algorithm)
    arch, graph, metrics, smells = (bundle["arch"], bundle["graph"],
                                    bundle["metrics"], bundle["smells"])

    violations = _check_rules(rules, arch, graph, metrics, smells)
    layers = _detect_layers(arch, graph)

    # Markdown
    lines = [f"# Architecture Validation: {bundle['repo'].name}", ""]
    if violations:
        lines += [f"❌ **{len(violations)} rule violation(s)**", "",
                  "| Rule | Type | Violation |", "|------|------|-----------|"]
        for v in violations:
            lines.append(f"| {v['rule']} | {v['type']} | {v['message']} |")
    else:
        lines.append("✅ **No rule violations.**" if rules.get("rules")
                     else "_No rules defined._")
    lines += ["", "## Layered-architecture check", ""]
    if layers["detected"]:
        lines.append(f"Detected a layered structure ({layers['coverage']:.0%} of components "
                     "map to a layer):")
        for n, lyr in sorted(layers["layers"].items(), key=lambda x: x[1]):
            lines.append(f"- `{n}` → **{lyr}**")
        if layers["upward_violations"]:
            lines += ["", f"⚠️ {len(layers['upward_violations'])} upward "
                      "dependency violation(s) (a lower layer depending on a higher one):"]
            for u in layers["upward_violations"]:
                lines.append(f"- {u}")
        else:
            lines.append("\nNo upward layer violations — dependencies point inward. 👍")
    else:
        lines.append(f"No clear layered/clean architecture detected "
                     f"(only {layers['coverage']:.0%} of components match layer names).")
    md = "\n".join(lines)

    print("\n" + md)
    if args.output:
        out = Path(args.output).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md)
        print(f"\nValidation report written to: {out}")

    emit_summary({
        "command": "validate",
        "repo": bundle["repo"].name,
        "rules_file": str(rules_path) if rules_path else None,
        "num_violations": len(violations),
        "violations": violations,
        "layers": layers,
    })

    if violations and args.fail_on == "error":
        sys.exit(1)


if __name__ == "__main__":
    main()
