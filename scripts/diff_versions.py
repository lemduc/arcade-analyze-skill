#!/usr/bin/env python3
"""arcade-analyze diff: compare a codebase's architecture between two git refs.

Quantifies architectural drift: which components were added/removed, how many
entities moved between components (refactors), possible splits/merges, the delta
on the quality metrics, and which smells are new in the later version.

    <ARCADE_AGENT_HOME>/.venv/bin/python diff_versions.py <repo> \
        --from v1.0 --to v2.0 [--language java]

`<repo>` must be a LOCAL git repository. We clone it to a temp directory and
check out the two refs there, so your working tree is never touched. Refs can be
tags, branches, or commit SHAs.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from _common import add_common_args, bootstrap, emit_summary


def _git(repo: Path, *args: str) -> str:
    """Run a git command in `repo`, returning stdout. Exits on failure."""
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        sys.exit(f"[arcade-analyze] git {' '.join(args)} failed:\n{proc.stderr.strip()}")
    return proc.stdout.strip()


def _smell_name(smell) -> str:
    """Human-readable smell type. smell_type is a str-Enum; .value is the clean
    label ("Dependency Cycle"), while str(enum) gives "SmellType.DEPENDENCY_CYCLE"."""
    return str(getattr(smell.smell_type, "value", smell.smell_type))


def _smell_key(smell) -> tuple:
    """Stable identity for a smell so we can diff smell sets across versions."""
    return (_smell_name(smell), tuple(sorted(smell.affected_components or [])))


def _recover_at_ref(clone: Path, ref: str, language: str | None,
                    source_root: str | None, algorithm: str):
    """Check out `ref` in the clone and run ingest -> parse -> recover -> smells -> metrics."""
    from arcade_agent.tools.compute_metrics import compute_metrics
    from arcade_agent.tools.detect_smells import detect_smells
    from arcade_agent.tools.ingest import ingest
    from arcade_agent.tools.parse import parse
    from arcade_agent.tools.recover import recover

    print(f"  [{ref}] checkout ...", flush=True)
    _git(clone, "checkout", "--quiet", ref)

    # Join source_root to the clone ourselves (ingest treats it as cwd-relative).
    source_dir = str((clone / source_root).resolve()) if source_root else str(clone)
    repo = ingest(source_dir, language=language)
    if not repo.source_files:
        sys.exit(f"[arcade-analyze] No source files found at ref '{ref}'. "
                 "Check --language / --source-root.")
    # use_cache=False: same paths across refs, content differs — don't reuse.
    graph = parse(str(repo.path), language=repo.language,
                  files=[str(f) for f in repo.source_files], use_cache=False)
    if graph.num_entities == 0:
        sys.exit(f"[arcade-analyze] No entities extracted at ref '{ref}'.")
    arch = recover(graph, algorithm=algorithm)
    smells = detect_smells(arch, graph)
    metrics = compute_metrics(arch, graph)
    print(f"        {graph.num_entities} entities, {len(arch.components)} components, "
          f"{len(smells)} smells", flush=True)
    return {
        "ref": ref, "language": repo.language,
        "num_entities": graph.num_entities, "num_edges": graph.num_edges,
        "arch": arch, "smells": smells,
        "metrics": {m.name: m.value for m in metrics},
    }


def _build_markdown(repo_name: str, frm: dict, to: dict, drift: dict,
                    new_smells: list, fixed_smells: list, algorithm: str) -> str:
    s = drift["summary"]
    matches = drift["matches"]
    added = [m["target"] for m in matches if not m["source"]]
    removed = [m["source"] for m in matches if not m["target"]]
    entities_moved = sum(
        len(m.get("entities_added", [])) + len(m.get("entities_removed", []))
        for m in matches if m["source"] and m["target"]
    )

    def delta(a, b):
        if a is None or b is None:
            return "—"
        d = b - a
        return f"+{d:.2f}" if d >= 0 else f"{d:.2f}"

    lines = [
        f"## Architecture Drift: {repo_name}  ({frm['ref']} → {to['ref']})",
        "",
        f"**Algorithm:** {algorithm.upper()} · **A2A similarity:** "
        f"{drift['overall_similarity']:.2f}  (1.0 = identical)",
        "",
        "| Metric | " + frm["ref"] + " | " + to["ref"] + " | Δ |",
        "|--------|------|------|---|",
        f"| Entities | {frm['num_entities']} | {to['num_entities']} | "
        f"{to['num_entities'] - frm['num_entities']:+d} |",
        f"| Components | {s['arch_a_components']} | {s['arch_b_components']} | "
        f"{s['arch_b_components'] - s['arch_a_components']:+d} |",
    ]
    for name in ("RCI", "TurboMQ"):
        a, b = frm["metrics"].get(name), to["metrics"].get(name)
        av = f"{a:.2f}" if a is not None else "—"
        bv = f"{b:.2f}" if b is not None else "—"
        lines.append(f"| {name} | {av} | {bv} | {delta(a, b)} |")
    lines += [
        f"| Smells | {len(frm['smells'])} | {len(to['smells'])} | "
        f"{len(to['smells']) - len(frm['smells']):+d} |",
        "",
        "### Structural changes",
        "",
    ]
    any_change = False
    if added:
        lines.append(f"- **Added components ({len(added)}):** `{'`, `'.join(added)}`")
        any_change = True
    if removed:
        lines.append(f"- **Removed components ({len(removed)}):** `{'`, `'.join(removed)}`")
        any_change = True
    if entities_moved:
        lines.append(f"- **{entities_moved}** entity movement(s) between components (refactors)")
        any_change = True
    if s["possible_merges"]:
        lines.append(f"- **{s['possible_merges']}** possible merge(s)")
        any_change = True
    if s["possible_splits"]:
        lines.append(f"- **{s['possible_splits']}** possible split(s)")
        any_change = True
    if not any_change:
        lines.append("- No structural changes detected.")
    lines.append("")

    lines.append("### Smell delta")
    lines.append("")
    if new_smells:
        lines.append(f"**New in {to['ref']} ({len(new_smells)}):**")
        for sm in new_smells:
            aff = ", ".join(sm.affected_components or [])
            lines.append(f"- [{sm.severity}] {_smell_name(sm)}: {aff}")
    if fixed_smells:
        lines.append(f"\n**Resolved since {frm['ref']} ({len(fixed_smells)}):**")
        for sm in fixed_smells:
            aff = ", ".join(sm.affected_components or [])
            lines.append(f"- {_smell_name(sm)}: {aff}")
    if not new_smells and not fixed_smells:
        lines.append("- No change in detected smells.")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(description="Diff a codebase's architecture across two git refs")
    p.add_argument("repo", help="Path to a LOCAL git repository")
    add_common_args(p)
    p.add_argument("--from", dest="frm", required=True, help="Older git ref (tag/branch/SHA)")
    p.add_argument("--to", dest="to", default="HEAD", help="Newer git ref (default: HEAD)")
    p.add_argument("--algorithm", "-a", default="pkg", help="Recovery algorithm (default: pkg)")
    p.add_argument("--source-root", default=None,
                   help="Sub-path to treat as the source root (e.g. src/main/java).")
    p.add_argument("--output", "-o", default=None,
                   help="Write the markdown drift report to this path (also printed to stdout).")
    p.add_argument("--min-similarity", type=float, default=None,
                   help="Gate: exit 1 if A2A similarity drops below this (e.g. 0.7).")
    p.add_argument("--max-new-smells", type=int, default=None,
                   help="Gate: exit 1 if more than this many new smells appear in --to.")
    args = p.parse_args()

    repo_path = Path(args.repo).expanduser().resolve()
    if not (repo_path / ".git").exists():
        sys.exit(f"[arcade-analyze] {repo_path} is not a git repository (no .git). "
                 "diff needs a local git repo to check out the two refs.")

    bootstrap(args.arcade_home)
    from arcade_agent.tools.compare import compare

    tmp = Path(tempfile.mkdtemp(prefix="arcade_diff_"))
    try:
        clone = tmp / repo_path.name
        print(f"Cloning {repo_path.name} (full history) to a temp dir ...", flush=True)
        subprocess.run(["git", "clone", "--quiet", str(repo_path), str(clone)], check=True)

        print(f"Analyzing ref '{args.frm}' ...", flush=True)
        frm = _recover_at_ref(clone, args.frm, args.language, args.source_root, args.algorithm)
        print(f"Analyzing ref '{args.to}' ...", flush=True)
        to = _recover_at_ref(clone, args.to, args.language, args.source_root, args.algorithm)

        drift = compare(frm["arch"], to["arch"])

        from_keys = {_smell_key(s) for s in frm["smells"]}
        to_keys = {_smell_key(s) for s in to["smells"]}
        new_smells = [s for s in to["smells"] if _smell_key(s) not in from_keys]
        fixed_smells = [s for s in frm["smells"] if _smell_key(s) not in to_keys]

        md = _build_markdown(repo_path.name, frm, to, drift,
                             new_smells, fixed_smells, args.algorithm)
        print("\n" + md)
        if args.output:
            out = Path(args.output).expanduser().resolve()
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(md)
            print(f"\nDrift report written to: {out}")

        matches = drift["matches"]
        emit_summary({
            "command": "diff",
            "repo": repo_path.name,
            "from_ref": args.frm,
            "to_ref": args.to,
            "algorithm": args.algorithm,
            "overall_similarity": drift["overall_similarity"],
            "from": {"num_entities": frm["num_entities"],
                     "num_components": drift["summary"]["arch_a_components"],
                     "metrics": frm["metrics"], "num_smells": len(frm["smells"])},
            "to": {"num_entities": to["num_entities"],
                   "num_components": drift["summary"]["arch_b_components"],
                   "metrics": to["metrics"], "num_smells": len(to["smells"])},
            "components_added": [m["target"] for m in matches if not m["source"]],
            "components_removed": [m["source"] for m in matches if not m["target"]],
            "new_smells": [{"type": _smell_name(s), "severity": str(s.severity),
                            "affected": s.affected_components} for s in new_smells],
            "fixed_smells": [{"type": _smell_name(s),
                              "affected": s.affected_components} for s in fixed_smells],
        })

        # CI gate: fail if drift exceeds thresholds.
        gate_failures = []
        if args.min_similarity is not None and drift["overall_similarity"] < args.min_similarity:
            gate_failures.append(
                f"A2A similarity {drift['overall_similarity']:.2f} < min {args.min_similarity}")
        if args.max_new_smells is not None and len(new_smells) > args.max_new_smells:
            gate_failures.append(
                f"{len(new_smells)} new smell(s) > max {args.max_new_smells}")
        if gate_failures:
            print("\n❌ Drift gate failed:", file=sys.stderr)
            for f in gate_failures:
                print(f"  - {f}", file=sys.stderr)
            sys.exit(1)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
