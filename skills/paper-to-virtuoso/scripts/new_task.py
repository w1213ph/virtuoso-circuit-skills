"""Create a task record for work in a new directory."""

import argparse
import json
import re
from pathlib import Path


ASSETS = Path(__file__).resolve().parents[1] / "assets"
DESIGN_MODES = {"paper", "design"}


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument(
        "--mode", choices=["paper", "design", "redraw", "environment", "export"], required=True
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", args.project):
        parser.error("Project prefix must use uppercase letters, digits and underscores")

    acceptance = json.loads((ASSETS / "acceptance-template.json").read_text(encoding="utf-8"))
    acceptance.update(task=args.project, mode=args.mode)
    templates = {}
    if args.mode in DESIGN_MODES:
        templates = {
            "DESIGN_PLAN.md": "paper-reproduction-template.md",
            "prelayout.json": "prelayout-template.json",
            "optimization-candidates.csv": "optimization-candidates.csv",
        }
    template_data = {name: (ASSETS / source).read_bytes() for name, source in templates.items()}

    try:
        args.out.mkdir(parents=True)
    except FileExistsError:
        parser.error("Task directory already exists; inspect existing work first")

    for name in ["evidence", "screenshots", "netlists", "simulation", "backup"]:
        (args.out / name).mkdir()

    requirements = {
        "project": args.project,
        "mode": args.mode,
        "purpose": None,
        "interfaces": [],
        "paper": None,
        "figure_numbers": [],
        "pdk_library": None,
        "process_version": None,
        "supply_v": None,
        "bandwidth_definition": None,
        "goals": [],
        "assumptions": [],
        "open_questions": [],
        "source_library": None,
        "target_library": args.project + "_DESIGN",
        "electrical_changes_authorized": False,
        "new_simulations_authorized": False,
    }
    write_json(args.out / "requirements.json", requirements)
    write_json(args.out / "acceptance.json", acceptance)
    for name, data in template_data.items():
        (args.out / name).write_bytes(data)
    write_json(
        args.out / "CONNECTION.local.json",
        {
            "connection_tool": "PROVIDE_LOCALLY",
            "workspace": "PROVIDE_LOCALLY",
            "credentials": "USE_LOCAL_CREDENTIAL_PROVIDER",
        },
    )
    task_note = (
        f"# {args.project}\n\n"
        "Read the repository AGENTS.md and paper-to-virtuoso/SKILL.md.\n"
        f"Mode: {args.mode}. Record the scope from the user's request in requirements.json.\n"
        "Preserve existing saved design and manual edits. "
        "Evidence is required to fill acceptance.json.\n"
    )
    if args.mode in DESIGN_MODES:
        task_note += "Use prelayout.json to record test conditions, targets and measured results.\n"
    (args.out / "TASK.md").write_text(task_note, encoding="utf-8")
    print("EMPTY_TASK_CREATED", args.out, "No Cadence operation or simulation was run.")


if __name__ == "__main__":
    main()
