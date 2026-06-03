#!/usr/bin/env python3
"""arcade-analyze c4: export the recovered architecture as C4 / Structurizr.

Architects document systems in the C4 model. This maps each recovered component
to a C4 Component (inside one Container = the codebase) and inter-component
dependencies to relationships, emitting:

  - <name>.puml  — C4-PlantUML (renders anywhere PlantUML runs)
  - <name>.dsl   — Structurizr DSL (for Structurizr / structurizr-lite)

    <ARCADE_AGENT_HOME>/.venv/bin/python export_c4.py <source> \
        [--language java] [-o out/dir-or-basename]
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from _common import add_common_args, bootstrap, emit_summary, recover_bundle


def _ident(name: str) -> str:
    """Sanitize a component name into a DSL/PlantUML identifier."""
    i = re.sub(r"[^A-Za-z0-9_]", "_", name)
    if i and i[0].isdigit():
        i = "c_" + i
    return i or "comp"


def _puml(repo_name, arch, deps) -> str:
    lines = [
        "@startuml",
        "!include https://raw.githubusercontent.com/plantuml-stdlib/C4-PlantUML/master/C4_Component.puml",
        f'title Component diagram — {repo_name} (recovered by arcade-analyze)',
        "",
        f'Container_Boundary(system, "{repo_name}") {{',
    ]
    for c in arch.components:
        resp = (c.responsibility or "component").replace('"', "'")
        lines.append(f'  Component({_ident(c.name)}, "{c.name}", '
                     f'"{len(c.entities)} entities", "{resp}")')
    lines.append("}")
    lines.append("")
    for src, tgt in deps:
        lines.append(f'Rel({_ident(src)}, {_ident(tgt)}, "depends on")')
    lines.append("@enduml")
    return "\n".join(lines)


def _dsl(repo_name, arch, deps) -> str:
    lines = ["workspace {", "    model {",
             f'        system = softwareSystem "{repo_name}" {{']
    for c in arch.components:
        resp = (c.responsibility or "component").replace('"', "'")
        lines.append(f'            {_ident(c.name)} = component "{c.name}" '
                     f'"{resp}" "{len(c.entities)} entities"')
    lines.append("        }")
    for src, tgt in deps:
        lines.append(f'        {_ident(src)} -> {_ident(tgt)} "depends on"')
    lines += [
        "    }",
        "    views {",
        "        component system {",
        "            include *",
        "            autolayout lr",
        "        }",
        "        theme default",
        "    }",
        "}",
    ]
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(description="Export recovered architecture as C4 / Structurizr")
    p.add_argument("source", help="Local source directory OR a git URL")
    add_common_args(p)
    p.add_argument("--algorithm", "-a", default="pkg", help="Recovery algorithm (default: pkg)")
    p.add_argument("--source-root", default=None, help="Sub-path treated as source root")
    p.add_argument("--output", "-o", default=None,
                   help="Output basename or dir. Default: ./arcade-report/<name>-c4{.puml,.dsl}")
    args = p.parse_args()

    bootstrap(args.arcade_home)
    print(f"Analyzing {args.source} ...", flush=True)
    bundle = recover_bundle(args.source, args.language, args.source_root, algorithm=args.algorithm)
    arch, graph, repo = bundle["arch"], bundle["graph"], bundle["repo"]
    deps = arch.component_dependencies(graph)

    # Resolve a basename to write <base>.puml and <base>.dsl.
    if args.output:
        o = Path(args.output).expanduser().resolve()
        base = o / f"{repo.name}-c4" if o.is_dir() else o.with_suffix("")
    else:
        base = Path.cwd() / "arcade-report" / f"{repo.name}-c4"
    base.parent.mkdir(parents=True, exist_ok=True)

    puml_path = base.with_suffix(".puml")
    dsl_path = base.with_suffix(".dsl")
    puml_path.write_text(_puml(repo.name, arch, deps))
    dsl_path.write_text(_dsl(repo.name, arch, deps))

    emit_summary({
        "command": "c4",
        "repo": repo.name,
        "num_components": len(arch.components),
        "num_relationships": len(deps),
        "c4_plantuml": str(puml_path),
        "structurizr_dsl": str(dsl_path),
    })
    print(f"\nC4-PlantUML: {puml_path}\nStructurizr DSL: {dsl_path}")
    print("Render the .puml at https://www.plantuml.com/plantuml or with the PlantUML CLI; "
          "open the .dsl with Structurizr Lite.")


if __name__ == "__main__":
    main()
