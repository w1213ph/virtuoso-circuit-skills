"""Tune an editable SKY130 ADC sampling driver and validate the frozen choice on independent mismatch draws."""

import argparse
import csv
import hashlib
import json
import math
import shutil
import sys
from pathlib import Path

from run import HERE, MIRROR, budget, digest, requirements
from bench import FIXED_OUTPUT_PF
from fetch_models import LOCK, prepare, verify as verify_models
from matching_design import family, native_views, pick
from matching_measure import calibration, check_row, diagnose, nominal, population
from mismatch import validate_run
from mismatch_bench import draw, require, seed_range
from mos_table import FIELDS, STATES, cache_record, characterize, command, read_curves, save_json, scans


LENGTHS = (0.5, 1.0, 1.5, 2.0)


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def approximately(a, b):
    if isinstance(a, dict):
        return (
            isinstance(b, dict) and a.keys() == b.keys() and all(approximately(v, b[k]) for k, v in a.items())
        )
    if isinstance(a, (tuple, list)):
        return (
            isinstance(b, (tuple, list))
            and len(a) == len(b)
            and all(approximately(x, y) for x, y in zip(a, b))
        )
    if type(a) is float and type(b) in (float, int):
        return math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-9)
    return type(a) is type(b) and a == b


def recorded(path, value, write):
    if write:
        save_json(path, value)
    else:
        require(approximately(value, read(path)), "Recomputed data differ at " + path.name)


def seed_blocks(first, unseen, confirmation, count):
    blocks = []
    used = set()
    for start, size in ((first, count), (unseen, count), (confirmation, min(256, 2 * count))):
        seeds = list(seed_range(start, size))
        require(used.isdisjoint(seeds), "All three seed blocks must be disjoint")
        used.update(seeds)
        blocks.append(seeds)
    return blocks


def characterization(root, recipe, ngspice):
    identity = recipe["identity"]
    tables = root / "characterization"
    if ngspice:
        tables.mkdir()
    for state in STATES:
        if ngspice:
            characterize(tables, recipe["model_path"], ngspice, state, identity, lengths=LENGTHS)
        path = tables / state[0]
        raw, devices = scans(recipe["model_path"], *state, lengths=LENGTHS)
        definition = dict(
            identity,
            lengths_um=list(LENGTHS),
            input_sha256=hashlib.sha256(raw.encode()).hexdigest(),
            reader_sha256=digest(MIRROR / "mos_table.py"),
        )
        files = ["input.spice", "ngspice.log", "table.csv"] + [tag + ".dat" for tag, *_ in devices]
        require(
            cache_record(path, definition, files) is not None, "Changed MOS characterization at " + state[0]
        )
        validate_run(path, raw)
        rows = read_curves(path, devices, state[2])
        with (path / "table.csv").open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            require(reader.fieldnames == list(FIELDS), "Unexpected MOS table fields")
            for row in rows:
                require(
                    next(reader, None) == {k: "" if v is None else str(v) for k, v in row.items()},
                    "Saved sizing table differs from the device sweeps",
                )
            require(next(reader, None) is None, "Extra device rows")
        if state[0] == "tt":
            typical = rows
    return typical


def inventory(root):
    return {
        p.relative_to(root).as_posix(): digest(p)
        for top in ("characterization", "designs", "validation", "confirmation")
        for p in sorted((root / top).rglob("*"))
        if p.is_file()
        and (
            p.suffix in {".dat", ".spice", ".sch", ".sym", ".csv"}
            or p.name
            in {"open.sh", "ngspice.log", "xschem.log", "sizing.json", "readback.json", "manifest.json"}
        )
    }


def selected_opener(name):
    return (
        '#!/bin/sh\nset -eu\nroot=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
        'exec sh "$root/designs/' + name + '/open.sh" "$@"\n'
    )


def analyze(root, recipe, spec, ngspice=None, xschem=None):
    writing = ngspice is not None
    require(
        recipe["schema_version"] == 1
        and recipe["model_revision"] == LOCK["revision"]
        and recipe["identity"]["model_lock_sha256"] == digest(MIRROR / "models.lock.json")
        and recipe["identity"]["ngspice_version_sha256"] == digest(root / "ngspice-version.txt"),
        "Changed solver or model identity",
    )
    reserve = recipe["reserve_percent"]
    require(
        type(reserve) in (int, float) and 0 <= reserve < 100,
        "Use a training reserve from 0 to less than 100 percent",
    )
    seeds, unseen, final_seeds = seed_blocks(
        recipe["training_seed"], recipe["validation_seed"], recipe["confirmation_seed"], recipe["samples"]
    )
    rows = characterization(root, recipe, ngspice)
    geometries = family(rows, spec)
    designs = root / "designs"
    if writing:
        designs.mkdir()
    circuits = {}
    fixture = None
    limit = budget(spec)["half_lsb_mV"]

    def measure_design(name, sizing, bank_seeds):
        nonlocal fixture
        stage = designs / name
        native, bench = native_views(stage, sizing, spec, xschem)
        if fixture is None:
            fixture = bench
        require(bench == fixture, "The testbench or transmission gate changed between candidates")
        circuits[name] = native
        corners = nominal(stage / "nominal", native, recipe["model_path"], spec, ngspice)
        pairs = calibration(
            stage / "calibration", native, recipe["model_path"], spec, sizing, corners["ss"], ngspice
        )
        bank = population(stage / "training", native, recipe["model_path"], spec, bank_seeds, ngspice)
        if writing:
            print(name, "training", bank["passed"], "/", bank["samples"], flush=True)
        return dict(
            bank,
            nominal=corners,
            calibration=pairs,
            area_um2=sizing["core_gate_area_um2"],
            pvt_pass=all(check_row(row, spec) for row in corners.values()),
        )

    def compare(label, names, bank_seeds):
        return {
            name: population(
                root / label / name, circuits[name], recipe["model_path"], spec, bank_seeds, ngspice
            )
            for name in dict.fromkeys(names)
        }

    training = {name: measure_design(name, sizing, seeds) for name, sizing in geometries.items()}
    recorded(root / "training.json", training, writing)
    diagnosis = diagnose(
        designs / "original" / "diagnosis",
        circuits["original"],
        recipe["model_path"],
        spec,
        training["original"]["worst_seed"],
        ngspice,
    )
    recorded(root / "diagnosis.json", diagnosis, writing)
    winner, reason = pick(training, limit, reserve)
    lock = dict(
        candidate=winner,
        reason=reason,
        training_sha256=digest(root / "training.json"),
        input_sha256=digest(root / "requirements.json"),
        recipe_sha256=digest(root / "recipe.json"),
        netlist_sha256=hashlib.sha256(circuits[winner].encode()).hexdigest(),
        validation_seeds=unseen,
    )
    recorded(root / "frozen-choice.json", lock, writing)
    heldout = compare("validation", ("original", winner), unseen)
    qualified = reason != "diagnostic_only" and heldout[winner]["passed"] == len(unseen)
    first_round = dict(candidate=winner, qualified=qualified, validation=heldout)
    recorded(root / "first-round.json", first_round, writing)
    final_candidate = winner
    repair_training = {}
    confirmation = {}
    skipped = []
    repair_reason = None
    if not qualified:
        # The first validation has now informed the redesign. It belongs to development.
        # A separate bank was reserved in the recipe before any simulations were run.
        alternatives = family(rows, spec, repair=True)
        ordered = sorted(alternatives, key=lambda n: (alternatives[n]["core_gate_area_um2"], n))
        for i, name in enumerate(ordered):
            repair_training[name] = measure_design(name, alternatives[name], seeds + unseen)
            final_candidate, repair_reason = pick(repair_training, limit, reserve)
            if repair_reason == "reserve_met":
                skipped = ordered[i + 1 :]
                break
        recorded(root / "repair-training.json", repair_training, writing)
        recorded(
            root / "frozen-repair.json",
            dict(
                candidate=final_candidate,
                reason=repair_reason,
                larger_unmeasured=skipped,
                training_sha256=digest(root / "repair-training.json"),
                earlier_round_sha256=digest(root / "first-round.json"),
                input_sha256=lock["input_sha256"],
                recipe_sha256=lock["recipe_sha256"],
                netlist_sha256=hashlib.sha256(circuits[final_candidate].encode()).hexdigest(),
                confirmation_seeds=final_seeds,
            ),
            writing,
        )
        confirmation = compare("confirmation", ("original", winner, final_candidate), final_seeds)
        qualified = repair_reason != "diagnostic_only" and confirmation[final_candidate]["passed"] == len(
            final_seeds
        )
    used = seeds + unseen + (final_seeds if confirmation else [])
    recorded(root / "vectors.json", {str(s): draw(s) for s in used}, writing)
    data = dict(
        trained_candidate=winner,
        first_round_qualified=first_round["qualified"],
        final_candidate=final_candidate,
        repair_reason=repair_reason,
        larger_unmeasured=skipped,
        confirmation=confirmation,
        selected=final_candidate if qualified else None,
        training_reason=reason,
        status="sample_limits_met" if qualified else "sample_limits_exceeded",
        limit_mV=limit,
        reserve_percent=reserve,
        validation=heldout,
        switch_area_um2=geometries[winner]["switch_gate_area_um2"],
    )
    recorded(root / "summary.json", data, writing)
    return data, dict(training, **repair_training)


def report(data, training):
    winner = data["trained_candidate"]
    lines = [
        "# ADC 采样驱动器的尺寸与独立复验",
        "",
        "同一采样电容、互补时钟和测试台，固定输出负载 {:g} pF。半个 LSB 为 {:.5g} mV。".format(
            FIXED_OUTPUT_PF, data["limit_mV"]
        ),
        "每个慢角样本同时检查双向的跟踪与保持误差，以及开关两态的相位裕度。训练阶段优先选留有 {:g}% 误差余量的较小方案。".format(
            data["reserve_percent"]
        ),
        "",
        "| 方案 | 放大器栅面积 (µm²) | 三个工艺角标称 | 开发样本通过 | 开发样本最坏误差 (mV) | 最大标称静态电流 (µA) |",
        "|---|---:|---|---:|---:|---:|",
    ]
    for name, row in training.items():
        current = max(max(v["track_quiescent_uA"], v["hold_quiescent_uA"]) for v in row["nominal"].values())
        lines.append(
            "| [{}](designs/{}/views/ADRV_OTA.sch) | {:.2f} | {} | {}/{} | {:.3f} | {:.2f} |".format(
                name,
                name,
                row["area_um2"],
                "达标" if row["pvt_pass"] else "超限",
                row["passed"],
                row["samples"],
                row["worst_error_mV"],
                current,
            )
        )
    lines += [
        "",
        "首轮独立复验前冻结了 `{}` 的网表。开关另占 {:.2f} µm² 栅面积。".format(
            winner, data["switch_area_um2"]
        ),
    ]
    for label, phase in (("validation", "首轮复验"), ("confirmation", "改型后的独立验收")):
        if not data[label]:
            continue
        if label == "confirmation":
            lines.extend(
                [
                    "",
                    "首轮出现超限后，两组已见种子合并用于改型，按面积从小到大评估。"
                    "第二次冻结了 `{}`，再使用预留的新种子验收。".format(data["final_candidate"]),
                    "[改型开发数据](repair-training.json) · [第二次冻结的网表和种子](frozen-repair.json)",
                ]
            )
        lines.extend(
            [
                "",
                "| {} | 同时达标 | 最大采样误差 (mV) | 最差种子和测量项 | 最低两态裕度 (°) |".format(phase),
                "|---|---:|---:|---|---:|",
            ]
        )
        for name, row in data[label].items():
            lines.append(
                "| {} | {}/{} | {:.3f} | [{} {}]({}/{}/seed-{}/track/transient.dat) | {:.2f} |".format(
                    name,
                    row["passed"],
                    row["samples"],
                    row["worst_error_mV"],
                    row["worst_seed"],
                    row["worst_metric"],
                    label,
                    name,
                    row["worst_seed"],
                    row["minimum_pm_deg"],
                )
            )
    winner = data["final_candidate"]
    if data["selected"]:
        lines += ["", "**实测通过的方案为 `{}`。**".format(winner)]
    else:
        lines += ["", "**本次训练和独立验证未得到全项达标的方案。** 下列入口打开冻结设计，便于复查超限工况。"]
    lines += [
        "",
        "在此结果目录运行 `sh {} bench`，把 `bench` 换成 `core` 或 `sampler` 可查看对应电路。".format(
            "open.sh" if data["selected"] else "designs/" + winner + "/open.sh"
        ),
        "[原方案分组失配诊断](diagnosis.json) · [冻结的选型与网表指纹](frozen-choice.json) · [逐种子训练数据和工作点](training.json) · [独立验证数据](summary.json)",
        "",
    ]
    return "\n".join(lines)


def run(args):
    spec = requirements(args.requirements)
    seed_blocks(args.training_seed, args.validation_seed, args.confirmation_seed, args.samples)
    require(
        math.isfinite(args.reserve_percent) and 0 <= args.reserve_percent < 100, "Invalid training reserve"
    )
    ng, xs = (shutil.which(x) for x in (args.ngspice, args.xschem))
    require(ng and xs, "Install ngspice and Xschem or supply executable paths")
    ng, xs = str(Path(ng).resolve()), str(Path(xs).resolve())
    out = args.out
    out.mkdir(parents=True, exist_ok=False)
    save_json(out / "status.json", {"status": "running"})
    try:
        lib = verify_models(args.models.resolve()) if args.models else prepare(out / "models")
        require('"' not in str(lib) and "\n" not in str(lib), "Unsupported model path")
        save_json(out / "requirements.json", spec)
        command([ng, "-n", "--version"], out, "ngspice-version.txt")
        identity = dict(
            model_lock_sha256=digest(MIRROR / "models.lock.json"),
            library_sha256=digest(lib),
            ngspice_version_sha256=digest(out / "ngspice-version.txt"),
            ngspice_binary_sha256=digest(Path(ng)),
        )
        recipe = dict(
            schema_version=1,
            identity=identity,
            model_revision=LOCK["revision"],
            model_path=str(lib),
            training_seed=args.training_seed,
            validation_seed=args.validation_seed,
            confirmation_seed=args.confirmation_seed,
            samples=args.samples,
            reserve_percent=args.reserve_percent,
            xschem_binary_sha256=digest(Path(xs)),
        )
        save_json(out / "recipe.json", recipe)
        data, training = analyze(out, recipe, spec, ng, xs)
        verify_models(lib.parent)
        if data["selected"]:
            (out / "open.sh").write_bytes(selected_opener(data["selected"]).encode("utf-8"))
        save_json(out / "evidence.sha256.json", inventory(out))
        (out / "RESULTS.md").write_text(report(data, training), encoding="utf-8")
        save_json(out / "status.json", {"status": data["status"]})
        return data
    except (OSError, ValueError, KeyError, IndexError, RuntimeError) as exc:
        save_json(out / "status.json", {"status": "incomplete", "reason": str(exc)})
        raise


def verify(root):
    require(
        read(root / "evidence.sha256.json") == inventory(root),
        "A required native source, simulation or raw trace changed",
    )
    data, training = analyze(root, read(root / "recipe.json"), requirements(root / "requirements.json"))
    opener = root / "open.sh"
    require(
        (opener.is_file() and opener.read_text(encoding="utf-8") == selected_opener(data["selected"]))
        if data["selected"]
        else not opener.exists(),
        "Root opener differs from the independently qualified design",
    )
    require(
        (root / "RESULTS.md").read_text(encoding="utf-8") == report(data, training)
        and read(root / "status.json") == {"status": data["status"]},
        "Report differs from independent replay",
    )
    return data


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest="action")
    create = commands.add_parser(
        "run", help="Build editable candidates and freeze a design before independent validation"
    )
    create.add_argument("--requirements", type=Path, default=HERE / "requirements.example.json")
    create.add_argument("--models", type=Path)
    create.add_argument("--xschem", default="xschem")
    create.add_argument("--ngspice", default="ngspice")
    create.add_argument("--training-seed", type=int, default=24701)
    create.add_argument("--validation-seed", type=int, default=54701)
    create.add_argument("--confirmation-seed", type=int, default=84701)
    create.add_argument(
        "--samples",
        type=int,
        default=32,
        help="Samples per initial bank, 2 to 256. Redesign confirmation uses twice this number, up to 256.",
    )
    create.add_argument(
        "--reserve-percent",
        type=float,
        default=10,
        help="Preferred training reserve as a percentage of half an LSB",
    )
    for command_parser in (
        create,
        commands.add_parser("verify", help="Recompute all measured banks from an existing result directory"),
    ):
        command_parser.add_argument("--out", type=Path, required=True)
    args = cli.parse_args()
    if not args.action:
        cli.error("Choose run or verify")
    try:
        args.out = args.out.resolve()
        data = run(args) if args.action == "run" else verify(args.out)
        print(data["status"], "| frozen:", data["final_candidate"], "| selected:", data["selected"])
        sys.exit(int(args.action == "run" and data["selected"] is None))
    except (OSError, ValueError, KeyError, IndexError, TypeError, RuntimeError) as exc:
        cli.exit(2, str(exc) + "\n")
