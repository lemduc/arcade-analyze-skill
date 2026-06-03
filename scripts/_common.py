"""Shared helpers for the arcade-analyze skill scripts.

Every entry script (analyze.py, compare_algorithms.py, diff_versions.py,
query.py) needs the same two things up front:

  1. Find the arcade-agent checkout (the venv has the deps, src/ has the code).
  2. Put <home>/src on sys.path so `import arcade_agent` works — we do this
     ourselves rather than trusting the editable install, whose .pth can point
     at a stale path.

These run with arcade-agent's venv interpreter
(`<ARCADE_AGENT_HOME>/.venv/bin/python`), which is where tree-sitter, networkx,
scipy, numpy, and jinja2 live. Resolution order for the home is always:
--arcade-home flag, then $ARCADE_AGENT_HOME, then error out (no hardcoded path,
so the public repo never leaks a personal directory).
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


def resolve_home(cli_home: str | None) -> Path:
    """Resolve the arcade-agent checkout from flag, env, or error."""
    candidate = cli_home or os.environ.get("ARCADE_AGENT_HOME")
    if not candidate:
        sys.exit(
            "[arcade-analyze] arcade-agent location is not set.\n"
            "  Pass --arcade-home /path/to/arcade-agent, or set the\n"
            "  ARCADE_AGENT_HOME environment variable to your arcade-agent checkout."
        )
    home = Path(candidate).expanduser().resolve()
    if not (home / "src" / "arcade_agent").is_dir():
        sys.exit(
            f"[arcade-analyze] Could not find arcade_agent at {home}/src.\n"
            f"  Pass --arcade-home /path/to/arcade-agent or set ARCADE_AGENT_HOME."
        )
    return home


def bootstrap(cli_home: str | None) -> Path:
    """Resolve the home and put <home>/src on sys.path. Returns the home path."""
    home = resolve_home(cli_home)
    src = str(home / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    return home


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
