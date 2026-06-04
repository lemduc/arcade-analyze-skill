#!/usr/bin/env python3
"""Score guardrail-eval result directories with `guard check`.

Usage:
    <ARCADE_AGENT_HOME>/.venv/bin/python evals/score.py \
        off=/tmp/eval/T1-off on=/tmp/eval/T1-on ...

Each arg is `label=dir`. Prints a per-run table and a JSON aggregate
(verdict + violations-by-rule), and an off-vs-on summary.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

# Make the skill's scripts importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from _common import bootstrap, recover_bundle  # noqa: E402
import _spec as S  # noqa: E402


def score_dir(d: str) -> dict:
    src = Path(d)
    spec_path = src / "architecture.spec.json"
    if not spec_path.is_file():
        return {"error": f"no spec in {d}"}
    spec = S.load_spec(spec_path)
    bundle = recover_bundle(str(src), "python", None, algorithm="pkg")
    graph = bundle["graph"]
    ent2comp = S.map_entities(graph, spec)
    cedges = S.component_edges(graph, ent2comp)
    violations = S.check_conformance(spec, graph, ent2comp, cedges,
                                     smells=bundle["smells"], metrics=bundle["metrics"])
    by_rule = Counter(v["rule"] for v in violations)
    errs = [v for v in violations if v["severity"] == "error"]
    return {
        "verdict": S.verdict(violations),
        "errors": len(errs),
        "warnings": len(violations) - len(errs),
        "by_rule": dict(by_rule),
        "messages": [v["message"] for v in violations],
    }


def main() -> None:
    bootstrap(None)
    runs = []
    for arg in sys.argv[1:]:
        label, _, d = arg.partition("=")
        runs.append((label, d, score_dir(d)))

    print(f"\n{'run':<10} {'verdict':<7} {'err':>3} {'warn':>4}  rules")
    print("-" * 64)
    for label, _d, r in runs:
        if "error" in r:
            print(f"{label:<10} ERR     {r['error']}")
            continue
        rules = ", ".join(f"{k}×{v}" for k, v in r["by_rule"].items()) or "—"
        print(f"{label:<10} {r['verdict']:<7} {r['errors']:>3} {r['warnings']:>4}  {rules}")

    # off vs on aggregate (labels start with 'off' / 'on')
    def agg(prefix):
        rs = [r for label, _d, r in runs if label.startswith(prefix) and "error" not in r]
        n = len(rs)
        return {
            "runs": n,
            "fail": sum(1 for r in rs if r["verdict"] == "FAIL"),
            "pass": sum(1 for r in rs if r["verdict"] == "PASS"),
            "total_errors": sum(r["errors"] for r in rs),
            "total_warnings": sum(r["warnings"] for r in rs),
        }

    summary = {"off": agg("off"), "on": agg("on")}
    print("\n=== AGGREGATE (off vs on) ===")
    print(json.dumps(summary, indent=2))
    print("\n=== FULL JSON ===")
    print(json.dumps([{"label": l, "dir": d, **r} for l, d, r in runs], indent=2))


if __name__ == "__main__":
    main()
