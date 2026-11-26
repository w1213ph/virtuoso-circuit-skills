"""Package the verified sampling-driver views, waveforms and runnable source as one offline project."""
import argparse
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from verify_results import read, verify
from driver_review import write_review


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SOURCE = {
    "examples/sky130-adc-driver": ("run.py", "driver.py", "bench.py", "precision.py", "verify_results.py", "driver_review.py",
        "requirements.example.json", "ADRV_NMOS.sym", "ADRV_PMOS.sym", "ADRV_SOURCE.sym", "ADRV_OTA.sch",
        "ADRV_OTA.sym", "ADRV_SAMPLER.sch", "ADRV_SAMPLER.sym", "TB_ADRV_SAMPLE.sch"),
    "examples/sky130-current-mirror": ("fetch_models.py", "models.lock.json", "mos_table.py", "mos_model.py",
                                       "native.py", "measure.py", "replay_lut.py"),
    "skills/paper-to-virtuoso/scripts": ("check_prelayout.py",),
}
ROOT_FILES = ("requirements.json", "budget.json", "fixture.json", "recipe.json", "status.json", "ngspice-version.txt",
              "comparison.json", "RESULTS.md")


def evidence_names(source, result):
    yield from ROOT_FILES
    if "optimization" in result:
        yield "optimization.json"
    for corner in result["characterization_points"]:
        folder = Path("characterization", corner)
        yield folder / "manifest.json"
        for name in read(source / folder / "manifest.json")["files"]:
            yield folder / name
    for variant in result["variants"]:
        path = Path(variant)
        for name in ("prelayout.json","before-simulation.json","checked.json","readback.json","sizing.json","open.sh"):
            yield path / name
        plan = read(source / path / "prelayout.json")
        names = set(plan["inputs"])
        for test in plan["tests"]:
            names.update(test["inputs"])
            names.update(plan["results"][test["id"]]["evidence"])
        names.add("views/xschem.log")
        for name in sorted(names):
            yield path / name


def build(source, dest, revision, xschem):
    if not re.fullmatch("[0-9a-f]{40}", revision):
        raise ValueError("Supply the complete source commit")
    if dest.exists() or dest == source or source in dest.parents:
        raise ValueError("Choose a new destination outside the measured project")
    if read(source/"recipe.json").get("schema_version") not in (3,4):
        raise ValueError("Build fresh measurements with the editable views frozen before simulation")
    result = verify(source, detailed=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dest.parent) as tmp:
        project = Path(tmp)/"project"
        evidence = project/"evidence"
        evidence.mkdir(parents=True)
        for name in evidence_names(source, result):
            target = evidence/name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source/name, target)
        # Replay the files that will actually ship, with no access to the originating model directory.
        delivered = verify(evidence, detailed=True)
        for directory, files in SOURCE.items():
            folder = project/"source"/directory
            folder.mkdir(parents=True)
            for name in files:
                shutil.copyfile(ROOT/directory/name,folder/name)
        for name in ("LICENSE", "NOTICE"):
            shutil.copyfile(ROOT/name,project/name)
        shutil.copyfile(evidence/"requirements.json",project/"my-driver.json")
        shutil.copyfile(HERE/"starter/sample.py",project/"sample.py")
        write_review(delivered, evidence, project, revision, xschem)
        project.rename(dest)


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--run", type=Path, required=True)
    cli.add_argument("--project", type=Path, required=True)
    cli.add_argument("--revision", required=True)
    cli.add_argument("--xschem", default="xschem")
    args = cli.parse_args()
    try:
        build(args.run.resolve(), args.project.resolve(), args.revision, args.xschem)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        cli.exit(2,str(exc) + "\n")
