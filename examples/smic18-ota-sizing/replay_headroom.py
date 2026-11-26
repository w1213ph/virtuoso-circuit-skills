"""Recompute paired tail saturation margins from the public, per-die terminal measurements."""
import argparse
import csv
import itertools
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "skills/paper-to-virtuoso/scripts"))
from device_headroom import margin_change


def compare(path, candidate="bias"):
    if candidate not in ("geometry", "bias"):
        raise ValueError("Select the detector-only geometry or the detector plus bias circuit")
    expected = set(itertools.product(range(1, 33), ("tt", "ss", "ff"), (0.95, 1.10),
                                     (-0.30, 0.0, 0.30), ("MCTP", "MCTN")))
    codes = {}
    worst = {}
    failures = {"baseline": set(), "candidate": set()}
    negatives = dict(baseline=0, candidate=0)
    recovered = introduced = 0
    with Path(path).open(encoding="utf8", newline="") as stream:
        for row in csv.DictReader(stream):
            die = int(row["sample"])
            code = int(row["code"])
            key = (die, row["corner"], float(row["input_cm_V"]), float(row["command_V"]), row["tail"])
            if key not in expected or not 0 <= code < 16 or code != codes.setdefault(die, code):
                raise ValueError("Duplicate or unexpected operating point, or the trim code changed on one die")
            expected.remove(key)
            values = [float(row[arm + "_" + field + "_mV"]) / 1000 for arm in ("baseline", candidate)
                      for field in ("vds", "vdsat")]
            if min(values) < 0:
                raise ValueError("The two forward-oriented NMOS tail measurements require positive voltages")
            change = margin_change(*values)
            for arm, field in (("baseline", "before_V"), ("candidate", "after_V")):
                mv = 1000 * change[field]
                if arm not in worst or mv < worst[arm]["margin_mV"]:
                    worst[arm] = dict(margin_mV=mv, sample=die, code=code, corner=key[1], input_cm_V=key[2],
                                      command_V=key[3], tail=key[4])
                if mv < 0:
                    negatives[arm] += 1
                    failures[arm].add(die)
            recovered += change["before_V"] < 0 <= change["after_V"]
            introduced += change["after_V"] < 0 <= change["before_V"]
    if expected:
        raise ValueError("Missing paired measurements across samples, corners, commands or tails")
    return dict(compared_with_baseline=candidate, points=1152, samples=32, negative_points=negatives,
                recovered=recovered, introduced=introduced,
                samples_with_both_tails_saturated={k:32-len(v) for k,v in failures.items()}, worst=worst)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", nargs="?", type=Path, default=HERE / "headroom-points.csv")
    parser.add_argument("--candidate", choices=("geometry", "bias"), default="bias")
    try:
        args = parser.parse_args()
        print(json.dumps(compare(args.csv, args.candidate), indent=2, allow_nan=False))
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(2, str(error) + "\n")
