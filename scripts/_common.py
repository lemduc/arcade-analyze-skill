"""Shared helpers for the arcade-analyze skill scripts.

Every entry script (analyze.py, compare_algorithms.py, diff_versions.py,
query.py) needs the same thing up front: a working `import arcade_agent`.
Two ways to get there, in resolution order:

  1. A development checkout: --arcade-home flag, then $ARCADE_AGENT_HOME.
     The script must then run with that checkout's venv interpreter
     (`<home>/.venv/bin/python`, where tree-sitter, networkx, scipy, numpy
     and jinja2 live); we put <home>/src on sys.path ourselves rather than
     trusting the editable install, whose .pth can point at a stale path.
  2. The PyPI package: `pip install arcade-agent` (Python >= 3.12). If no
     checkout is configured and `arcade_agent` is importable in the running
     interpreter, the scripts just use it — plain `python3` works.

If neither is available, error out with guidance (no hardcoded path, so the
public repo never leaks a personal directory).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# Markers the skill greps for to lift the machine-readable summary out of stdout.
SUMMARY_BEGIN = "===ARCADE_SUMMARY_JSON==="
SUMMARY_END = "===END_ARCADE_SUMMARY_JSON==="


def resolve_home(cli_home: str | None) -> Path | None:
    """Resolve the arcade-agent checkout from flag or env; None if unset."""
    candidate = cli_home or os.environ.get("ARCADE_AGENT_HOME")
    if not candidate:
        return None
    home = Path(candidate).expanduser().resolve()
    if not (home / "src" / "arcade_agent").is_dir():
        sys.exit(
            f"[arcade-analyze] Could not find arcade_agent at {home}/src.\n"
            f"  Pass --arcade-home /path/to/arcade-agent or set ARCADE_AGENT_HOME."
        )
    return home


def bootstrap(cli_home: str | None) -> Path | None:
    """Make `import arcade_agent` work.

    A configured checkout (--arcade-home / $ARCADE_AGENT_HOME) wins: its src/
    goes on sys.path and the caller is expected to be running the checkout's
    venv interpreter. Otherwise fall back to a pip-installed arcade-agent in
    the running interpreter. Returns the checkout path, or None in pip mode.
    """
    home = resolve_home(cli_home)
    if home is not None:
        src = str(home / "src")
        if src not in sys.path:
            sys.path.insert(0, src)
        return home

    try:
        import arcade_agent  # noqa: F401 - probing the installed package
        return None
    except ImportError:
        hint = ""
        if sys.version_info < (3, 12):
            hint = (f"  Note: this interpreter is Python "
                    f"{sys.version_info.major}.{sys.version_info.minor}; "
                    "arcade-agent needs Python >= 3.12.\n")
        sys.exit(
            "[arcade-analyze] arcade-agent is not available.\n"
            "  Easiest: pip install arcade-agent   (needs Python >= 3.12),\n"
            "  then re-run this script with that interpreter.\n"
            + hint +
            "  Or use a development checkout: pass --arcade-home "
            "/path/to/arcade-agent\n"
            "  (or set ARCADE_AGENT_HOME) and run with its venv interpreter."
        )


def add_common_args(parser: argparse.ArgumentParser) -> None:
    """Add the flags every script shares."""
    parser.add_argument(
        "--arcade-home", default=None,
        help="Path to the arcade-agent repo (overrides $ARCADE_AGENT_HOME).",
    )
    parser.add_argument(
        "--language", "-l", default=None,
        help="Language: java, python, c, cpp, typescript. Auto-detected if omitted.",
    )


def open_in_browser(path: Path) -> None:
    """Best-effort open a file in the default browser. Never fails the run."""
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
        elif sys.platform.startswith("linux"):
            subprocess.run(["xdg-open", str(path)], check=False)
        elif sys.platform.startswith("win"):
            os.startfile(str(path))  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001 - best-effort
        print(f"[arcade-analyze] Could not auto-open report: {exc}", file=sys.stderr)


def emit_summary(summary: dict) -> None:
    """Print the machine-readable summary block the skill parses from stdout."""
    print(f"\n{SUMMARY_BEGIN}")
    print(json.dumps(summary, indent=2, default=str))
    print(SUMMARY_END)


# Generic directory names that aren't a useful project label on their own.
_GENERIC_DIR_NAMES = {
    "java", "kotlin", "scala", "main", "src", "srcs", "app", "lib",
    "source", "sources", "python", "py", "go", "cmd", "pkg",
}


def _friendly_name(source: str, fallback: str) -> str:
    """Derive a human project name. ingest() uses the dir basename, which for a
    Maven layout is 'java' (from src/main/java). Climb to the first meaningful
    ancestor so reports/filenames read 'arcade_core', not 'java'."""
    if source.startswith(("http://", "https://", "git@")):
        return fallback
    p = Path(source).expanduser().resolve()
    for cand in [p, *p.parents]:
        if cand.name and cand.name.lower() not in _GENERIC_DIR_NAMES:
            return cand.name
    return fallback


def ingest_and_parse(source: str, language: str | None,
                     source_root: str | None = None, use_cache: bool = True):
    """Run the shared front of the pipeline: ingest then parse.

    Returns (repo, graph). Exits with a clear message if nothing parseable is
    found. Imports happen here so callers only need to have called bootstrap().
    """
    from arcade_agent.tools.ingest import ingest
    from arcade_agent.tools.parse import parse

    # arcade-agent's ingest() treats source_root as relative to the cwd, not the
    # repo, which is surprising and breaks from other directories. For a local
    # source we join it ourselves and pass the resolved sub-path as the source;
    # for a git URL we hand it to ingest natively (can't join before cloning).
    native_source_root = source_root
    if source_root and Path(source).is_dir():
        source = str((Path(source) / source_root).resolve())
        native_source_root = None

    repo = ingest(source, language=language, source_root=native_source_root)
    repo.name = _friendly_name(source, repo.name)
    print(f"      {len(repo.source_files)} source files | "
          f"language={repo.language} | version={repo.version}", flush=True)
    if not repo.source_files:
        sys.exit("[arcade-analyze] No source files found. Check the path/language.")

    graph = parse(str(repo.path), language=repo.language,
                  files=[str(f) for f in repo.source_files], use_cache=use_cache)
    print(f"      {graph.num_entities} entities, {graph.num_edges} edges", flush=True)
    if graph.num_entities == 0:
        sys.exit("[arcade-analyze] No entities extracted. Nothing to recover.")
    return repo, graph


def recover_bundle(source: str, language: str | None, source_root: str | None = None,
                   algorithm: str = "pkg", num_clusters: int | None = None,
                   use_llm: bool = False, use_cache: bool = True):
    """Run the full read path: ingest -> parse -> recover -> smells -> metrics.

    Returns a dict bundle with repo, graph, arch, smells, metrics. This is the
    common front-end for the architect-output scripts (summary, dsm, c4,
    refactor, validate, ...) so they don't each re-implement the pipeline.
    """
    from arcade_agent.tools.compute_metrics import compute_metrics
    from arcade_agent.tools.detect_smells import detect_smells
    from arcade_agent.tools.recover import recover

    repo, graph = ingest_and_parse(source, language, source_root, use_cache=use_cache)
    print(f"      recovering ({algorithm}) ...", flush=True)
    kwargs = {"num_clusters": num_clusters} if num_clusters is not None else {}
    arch = recover(graph, algorithm=algorithm, **kwargs)
    smells = detect_smells(arch, graph, use_llm=use_llm)
    metrics = compute_metrics(arch, graph)
    print(f"      {len(arch.components)} components, {len(smells)} smells", flush=True)
    return {"repo": repo, "graph": graph, "arch": arch,
            "smells": smells, "metrics": metrics}


def smell_name(smell) -> str:
    """Human-readable smell type. smell_type is a str-Enum whose .value is the
    clean label ('Dependency Cycle'); str(enum) gives 'SmellType.DEPENDENCY_CYCLE'."""
    return str(getattr(smell.smell_type, "value", smell.smell_type))
