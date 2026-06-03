#!/usr/bin/env python3
"""arcade-analyze summary: an executive, stakeholder-grade architecture summary.

The interactive HTML report is a developer dashboard. This produces the
narrative an architect walks into a review with: a single health score, the
codebase snapshot, the top findings in plain English, and concrete recommended
actions — as markdown you can paste into a doc or slide.

    <ARCADE_AGENT_HOME>/.venv/bin/python summary_report.py <source> \
        [--language java] [-o summary.md]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from _common import add_common_args, bootstrap, emit_summary, recover_bundle, smell_name

SEVERITY_PENALTY = {"high": 9, "medium": 4, "low": 2}

# Plain-English remediation per smell type, used when the detector didn't attach
# its own suggestion. Keyed by the clean smell label.
REMEDIATION = {
    "Dependency Cycle":
        "Break the cycle: introduce an interface or invert one dependency so the "
        "components form a layer instead of a loop. Cycles block independent "
        "release, testing, and reasoning.",
    "Concern Overload":
        "Split this component along its sub-responsibilities. It owns many "
        "entities with little internal cohesion, a sign it's really several "
        "modules wearing one name.",
    "Scattered Parasitic Functionality":
        "Consolidate the scattered concern into a single component so a change to "
        "it touches one place instead of many.",
    "Link/Upstream Overload":
        "Reduce fan-in: this component is a bottleneck many others depend on. "
        "Introduce a narrower interface (facade) or split it so changes don't "
        "ripple across the system.",
}


def _metric(metrics, name):
    for m in metrics:
        if m.name == name:
            return m.value
    return None


def _grade(score: int) -> str:
    return ("A" if score >= 85 else "B" if score >= 70 else
            "C" if score >= 55 else "D" if score >= 40 else "F")


def _health(metrics, smells) -> tuple[int, dict]:
    """Composite 0-100 health score: modularity quality minus a smell penalty."""
    rci = _metric(metrics, "RCI") or 0.0
    turbomq = _metric(metrics, "TurboMQ") or 0.0
    modularity = 0.5 * rci + 0.5 * turbomq           # 0..1
    base = 100.0 * modularity
    penalty = min(40, sum(SEVERITY_PENALTY.get(s.severity, 3) for s in smells))
    score = int(round(max(0.0, min(100.0, base - penalty))))
    return score, {"rci": rci, "turbomq": turbomq,
                   "modularity_base": round(base, 1), "smell_penalty": penalty}


def _findings(bundle) -> list[dict]:
    """Top plain-English findings, most important first."""
    arch, graph, smells, metrics = (bundle["arch"], bundle["graph"],
                                    bundle["smells"], bundle["metrics"])
    findings: list[dict] = []
    total = graph.num_entities or 1

    # 1. Concentration: a single component holding a large share of the code.
    if arch.components:
        biggest = max(arch.components, key=lambda c: len(c.entities))
        share = len(biggest.entities) / total
        if share >= 0.30:
            findings.append({
                "severity": "high" if share >= 0.45 else "medium",
                "title": f"`{biggest.name}` concentrates {share:.0%} of the codebase",
                "detail": f"{len(biggest.entities)} of {total} entities live in one "
                          f"component. Large mono-components are hard to own, test, and "
                          f"evolve independently.",
            })

    # 2. Each high/medium smell becomes a finding (high first).
    for sm in sorted(smells, key=lambda s: {"high": 0, "medium": 1, "low": 2}.get(s.severity, 3)):
        findings.append({
            "severity": sm.severity,
            "title": f"{smell_name(sm)}"
                     + (f" ({', '.join(sm.affected_components)})" if sm.affected_components else ""),
            "detail": sm.description or "",
        })

    # 3. Metric reading.
    rci, tmq = _metric(metrics, "RCI"), _metric(metrics, "TurboMQ")
    if tmq is not None and tmq < 0.4:
        findings.append({
            "severity": "medium",
            "title": f"Low modularization quality (TurboMQ {tmq:.2f})",
            "detail": "Components are weakly cohesive and/or tightly coupled to each "
                      "other — the recovered boundaries don't cleanly separate concerns.",
        })
    return findings


def _recommendations(smells) -> list[str]:
    seen, recs = set(), []
    for sm in sorted(smells, key=lambda s: {"high": 0, "medium": 1, "low": 2}.get(s.severity, 3)):
        label = smell_name(sm)
        if label in seen:
            continue
        seen.add(label)
        text = sm.suggestion.strip() if getattr(sm, "suggestion", "") else ""
        recs.append(text or REMEDIATION.get(label, f"Address the {label} smell."))
    return recs


def _markdown(bundle, score, breakdown, findings, recs) -> str:
    repo, graph, arch = bundle["repo"], bundle["graph"], bundle["arch"]
    metrics, smells = bundle["metrics"], bundle["smells"]
    grade = _grade(score)
    sev_counts = {s: sum(1 for x in smells if x.severity == s) for s in ("high", "medium", "low")}

    lines = [
        f"# Architecture Summary: {repo.name}",
        "",
        f"**Health: {score}/100 (grade {grade})**  ·  {len(arch.components)} components · "
        f"{graph.num_entities} entities · {len(smells)} smells "
        f"({sev_counts['high']} high / {sev_counts['medium']} medium / {sev_counts['low']} low)",
        "",
        "| Indicator | Value | Reading |",
        "|-----------|-------|---------|",
    ]
    rci, tmq = _metric(metrics, "RCI"), _metric(metrics, "TurboMQ")
    if rci is not None:
        lines.append(f"| RCI (coverage) | {rci:.2f} | {'good' if rci >= 0.7 else 'weak'} |")
    if tmq is not None:
        lines.append(f"| TurboMQ (modularity) | {tmq:.2f} | "
                     f"{'cohesive' if tmq >= 0.6 else 'moderate' if tmq >= 0.4 else 'tangled'} |")
    lines += ["", "## Top findings", ""]
    if findings:
        for f in findings:
            lines.append(f"- **[{f['severity']}] {f['title']}** — {f['detail']}")
    else:
        lines.append("- No significant findings. The architecture looks healthy.")
    lines += ["", "## Recommended actions", ""]
    if recs:
        for i, r in enumerate(recs, 1):
            lines.append(f"{i}. {r}")
    else:
        lines.append("No remediation needed.")
    lines += ["", "## Components", "",
              "| Component | Entities | Share |", "|-----------|----------|-------|"]
    total = graph.num_entities or 1
    for c in sorted(arch.components, key=lambda c: -len(c.entities)):
        lines.append(f"| {c.name} | {len(c.entities)} | {len(c.entities)/total:.0%} |")
    lines += ["", "---",
              "*Generated by arcade-analyze. Health = modularity (RCI+TurboMQ) "
              "minus a severity-weighted smell penalty; a signal, not a verdict.*"]
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(description="Stakeholder-grade architecture summary")
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

    score, breakdown = _health(bundle["metrics"], bundle["smells"])
    findings = _findings(bundle)
    recs = _recommendations(bundle["smells"])
    md = _markdown(bundle, score, breakdown, findings, recs)

    print("\n" + md)
    if args.output:
        out = Path(args.output).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md)
        print(f"\nSummary written to: {out}")

    emit_summary({
        "command": "summary",
        "repo": bundle["repo"].name,
        "health_score": score,
        "grade": _grade(score),
        "health_breakdown": breakdown,
        "num_components": len(bundle["arch"].components),
        "num_entities": bundle["graph"].num_entities,
        "findings": findings,
        "recommendations": recs,
    })


if __name__ == "__main__":
    main()
