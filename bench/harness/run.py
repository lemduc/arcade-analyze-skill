#!/usr/bin/env python3
"""ArchAgentBench harness: run AI agents on build/extend tasks, with vs. without
the architecture guardrail, and score conformance automatically.

For each (task x model x condition x rep) it copies the task seed to an isolated
workdir, drives a headless `claude -p` agent, then scores the result against the
task's architecture contract with the deterministic guard engine. One JSONL record
per run.

Run with arcade-agent's venv interpreter:
    ARCADE_AGENT_HOME=/path/to/arcade-agent \
    /path/to/arcade-agent/.venv/bin/python harness/run.py \
        --task tasks/gf-orders-write-py --models haiku --conditions off,on \
        --reps 5 --out results/run1.jsonl

Conditions:
  off  -- agent gets the feature + folder roles only (no contract, no guardrail).
  on   -- agent additionally gets the dependency rules and the guard CLI workflow
          (propose before coding, preview before cross-component imports, check+fix).
Both are scored against the same canonical contract.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent.parent       # arcade-analyze-skill/
SCRIPTS = SKILL / "scripts"
sys.path.insert(0, str(SCRIPTS))

ARCADE_HOME = os.environ.get("ARCADE_AGENT_HOME", "")
PY = f"{ARCADE_HOME}/.venv/bin/python"
GUARD = str(SCRIPTS / "guard.py")


def off_prompt(task: dict, workdir: Path) -> str:
    return (
        f"Build a feature from scratch in the Python project at {workdir} "
        f"(currently empty package folders). {task['folder_roles']} "
        f"Feature: {task['feature']} Make it work."
    )


def on_prompt(task: dict, workdir: Path) -> str:
    return (
        f"Build a feature from scratch in the Python project at {workdir}. "
        f"This project has an architecture contract at {workdir}/architecture.spec.json "
        f"enforced by arcade-guard. Contract: {task['rules_summary']} "
        f"Use these commands: PY=\"{PY}\"; GUARD=\"{GUARD}\". "
        f"Steps: (1) run \"$PY\" \"$GUARD\" propose {workdir} --intent \"{task['id']}\"; "
        f"(2) before any cross-component import run \"$PY\" \"$GUARD\" preview {workdir} --from <A> --to <B>; "
        f"(3) after coding run \"$PY\" \"$GUARD\" check {workdir} --arcade-home {ARCADE_HOME} "
        f"and fix any ERROR until PASS. {task['folder_roles']} "
        f"Feature: {task['feature']} Make it work and conformant."
    )


def run_agent(prompt: str, workdir: Path, model: str) -> dict:
    t0 = time.perf_counter()
    proc = subprocess.run(
        ["claude", "-p", prompt, "--model", model,
         "--permission-mode", "bypassPermissions"],
        cwd=str(workdir), capture_output=True, text=True,
    )
    return {"returncode": proc.returncode,
            "wall_s": round(time.perf_counter() - t0, 1),
            "tail": (proc.stdout or proc.stderr)[-400:]}


def score(workdir: Path, spec_path: Path) -> dict:
    """Deterministic conformance score against the canonical contract."""
    import _spec as S
    from _common import recover_bundle
    spec = S.load_spec(spec_path)
    try:
        bundle = recover_bundle(str(workdir), "python", None, algorithm="pkg", use_cache=False)
    except SystemExit:
        return {"verdict": "NO_CODE", "violations": [], "n_entities": 0}
    g = bundle["graph"]
    ent2comp = S.map_entities(g, spec)
    cedges = S.component_edges(g, ent2comp)
    viol = S.check_conformance(spec, g, ent2comp, cedges,
                               smells=bundle["smells"], metrics=bundle["metrics"])
    return {"verdict": S.verdict(viol),
            "n_entities": g.num_entities,
            "violations": [{"rule": v["rule"], "severity": v["severity"],
                            "message": v["message"]} for v in viol]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True, help="Path to a task directory")
    ap.add_argument("--models", default="haiku", help="Comma-separated (haiku,sonnet)")
    ap.add_argument("--conditions", default="off,on", help="Comma-separated (off,on)")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--out", default="results/run.jsonl")
    args = ap.parse_args()

    if not ARCADE_AGENT_HOME_ok():
        sys.exit("[bench] Set ARCADE_AGENT_HOME to your arcade-agent checkout.")

    from _common import bootstrap
    bootstrap(ARCADE_HOME)  # put arcade-agent/src on sys.path for scoring

    task_dir = Path(args.task).resolve()
    task = json.loads((task_dir / "task.json").read_text())
    spec_path = task_dir / "architecture.spec.json"
    seed = task_dir / "seed"
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    conditions = [c.strip() for c in args.conditions.split(",") if c.strip()]
    tmp_root = Path(tempfile.mkdtemp(prefix="archbench_"))
    records = []
    try:
        for model in models:
            for cond in conditions:
                for rep in range(1, args.reps + 1):
                    wd = tmp_root / f"{task['id']}-{model}-{cond}-{rep}"
                    shutil.copytree(seed, wd)
                    if cond == "on":
                        shutil.copy(spec_path, wd / "architecture.spec.json")
                    prompt = on_prompt(task, wd) if cond == "on" else off_prompt(task, wd)
                    print(f">>> {task['id']} | {model} | {cond} | rep {rep}", flush=True)
                    agent = run_agent(prompt, wd, model)
                    sc = score(wd, spec_path)  # always vs canonical contract
                    rec = {"task": task["id"], "model": model, "condition": cond,
                           "rep": rep, "verdict": sc["verdict"],
                           "conformant": sc["verdict"] == "PASS",
                           "n_violations": len(sc["violations"]),
                           "violations": sc["violations"], "n_entities": sc["n_entities"],
                           "agent_rc": agent["returncode"], "wall_s": agent["wall_s"]}
                    records.append(rec)
                    with out.open("a") as f:
                        f.write(json.dumps(rec) + "\n")
                    print(f"    -> {sc['verdict']} ({len(sc['violations'])} viol, "
                          f"{sc['n_entities']} entities, {agent['wall_s']}s)", flush=True)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    # Aggregate per condition.
    print("\n=== aggregate (violation = not PASS, excluding NO_CODE) ===")
    for cond in conditions:
        rs = [r for r in records if r["condition"] == cond and r["verdict"] != "NO_CODE"]
        nv = sum(1 for r in rs if not r["conformant"])
        print(f"  {cond:4s}: {nv}/{len(rs)} runs introduced a violation "
              f"({(100*nv/len(rs)) if rs else 0:.0f}%)")
    print(f"\nResults: {out}")


def ARCADE_AGENT_HOME_ok() -> bool:
    return bool(ARCADE_HOME) and (Path(ARCADE_HOME) / "src" / "arcade_agent").is_dir()


if __name__ == "__main__":
    main()
