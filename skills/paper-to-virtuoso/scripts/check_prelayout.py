"""Snapshot, validate and compare pre-layout measurement records."""

import argparse
import csv
import hashlib
import itertools
import json
import math
import operator
from pathlib import Path


COMPARISONS = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
    "==": operator.eq,
}

NOISE_DENSITIES = {"V/sqrt(Hz)": "_V_per_sqrt_Hz", "V2/Hz": "_V2_per_Hz"}
NOISE_RMS_UNITS = {"V RMS": 1, "mV RMS": 1e3, "uV RMS": 1e6, "nV RMS": 1e9}


def validate_recompute(spec, unit):
    if not isinstance(spec, dict) or set(spec) != {"method", "file", "axis", "density", "density_unit", "band_Hz"}:
        raise ValueError("Specify method, file, axis, density, density_unit and band_Hz for raw replay")
    if spec["method"] != "band_noise_rms" or unit not in NOISE_RMS_UNITS:
        raise ValueError("band_noise_rms requires a voltage RMS output unit")
    suffix = NOISE_DENSITIES.get(spec["density_unit"]) if isinstance(spec["density_unit"], str) else None
    if (not text_present(spec["file"]) or spec["axis"] != "frequency_Hz" or not suffix
            or not text_present(spec["density"]) or not spec["density"].endswith(suffix)):
        raise ValueError("Declare a frequency_Hz axis and a voltage-noise density column with matching units")
    band = spec["band_Hz"]
    if not isinstance(band, list) or len(band) != 2 or not all(finite_number(x) for x in band) or not 0 <= band[0] < band[1]:
        raise ValueError("band_Hz needs increasing nonnegative bounds in Hz")


def band_noise_rms(path, spec, unit):
    """Integrate a one-sided voltage PSD, linear in power versus Hz between samples."""
    low, high = spec["band_Hz"]
    square = spec["density_unit"] == "V/sqrt(Hz)"
    first = previous = None
    intervals = []
    points = 0
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = csv.reader(stream, strict=True)
        columns = next(rows, [])
        if (not columns or len(set(columns)) != len(columns) or
                spec["axis"] not in columns or spec["density"] not in columns):
            raise ValueError("Missing or duplicate CSV channel. Check the port and spectral density unit.")
        axis, channel = columns.index(spec["axis"]), columns.index(spec["density"])
        for row in rows:
            if len(row) != len(columns):
                raise ValueError(f"Incomplete CSV row at line {rows.line_num}")
            frequency, density = float(row[axis]), float(row[channel])
            power = density * density if square else density
            if (not math.isfinite(frequency) or frequency < 0 or not math.isfinite(power) or density < 0
                    or (previous and frequency <= previous[0])):
                raise ValueError(f"Invalid noise sample at line {rows.line_num}. Check frequency order and finite nonnegative density.")
            if first is None:
                first = frequency
            if previous:
                f0, p0 = previous
                left, right = max(f0, low), min(frequency, high)
                if left < right:
                    left_power = p0 + (power - p0) * ((left - f0) / (frequency - f0))
                    right_power = p0 + (power - p0) * ((right - f0) / (frequency - f0))
                    intervals.append((0.5 * left_power + 0.5 * right_power) * (right - left))
            previous = frequency, power
            points += 1
    if points < 2 or first > low or previous[0] < high:
        raise ValueError("The measured spectrum does not cover both limits. Extend the sweep or change the specified band.")
    value = math.sqrt(math.fsum(intervals)) * NOISE_RMS_UNITS[unit]
    if not math.isfinite(value):
        raise ValueError("Integrated noise exceeds the representable numeric range")
    return value, {"method": spec["method"], "file": spec["file"], "density": spec["density"],
                   "band_Hz": [low, high], "spectrum_points": points, "integrated_intervals": len(intervals)}


def text_present(value):
    return isinstance(value, str) and bool(value.strip())


def finite_number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def check_paths(paths, label):
    if not isinstance(paths, list) or not all(text_present(path) for path in paths):
        raise ValueError(f"{label} must be an array of file paths")


def condition_value(value):
    # JSON booleans and numbers have distinct meanings for a mode selector.
    if type(value) is bool or text_present(value):
        return json.dumps(value, ensure_ascii=False)
    if finite_number(value):
        return str(int(value)) if type(value) is int or value.is_integer() else repr(value)
    return None


def required_sweeps(plan):
    sweeps = plan.get("required_sweeps", [])
    if not isinstance(sweeps, list):
        raise ValueError("required_sweeps must be a list")
    for sweep in sweeps:
        if not isinstance(sweep, dict) or not text_present(sweep.get("metric")):
            raise ValueError("Each required sweep needs a metric")
        axes = sweep.get("axes")
        if not isinstance(axes, dict) or not axes or not all(text_present(k) for k in axes):
            raise ValueError("A required sweep needs named condition axes")
        for key, values in axes.items():
            if not isinstance(values, list) or not values:
                raise ValueError(f"Empty or invalid sweep axis: {key}")
            keys = [condition_value(v) for v in values]
            if None in keys or len(keys) != len(set(keys)):
                raise ValueError(f"Sweep values must be distinct finite scalars: {key}")
    return sweeps


def sweep_coverage(plan):
    reports = []
    for group in required_sweeps(plan):
        keys = sorted(group["axes"])
        values = {k: {condition_value(v): v for v in group["axes"][k]} for k in keys}
        needed = 1
        for axis in values.values():
            needed *= len(axis)
        planned = set()
        for test in plan["tests"]:
            if test["metric"] != group["metric"] or test.get("role") == "diagnostic":
                continue
            point = tuple(condition_value(test["conditions"].get(k)) for k in keys)
            if all(v in values[k] for k, v in zip(keys, point)):
                planned.add(point)
        missing = needed - len(planned)
        examples = []
        if missing:
            absent = (pt for pt in itertools.product(*(values[k] for k in keys)) if pt not in planned)
            examples = [{k: values[k][v] for k, v in zip(keys, pt)} for pt in itertools.islice(absent, 8)]
        contract = [group["metric"], {k: sorted(values[k]) for k in keys}]
        reports.append({"metric": group["metric"], "contract_sha256": record_hash(contract), "required": needed,
                        "planned": len(planned), "missing": missing, "missing_examples": examples})
    return reports


def load_plan(path):
    plan = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(plan, dict) or type(plan.get("schema_version")) is not int:
        raise ValueError("The plan must be an object with schema_version 1")
    if plan["schema_version"] != 1:
        raise ValueError(f"Unsupported schema_version {plan['schema_version']}")
    check_paths(plan.get("inputs"), "inputs")
    check_paths(plan.get("reference_inputs", []), "reference_inputs")
    if not isinstance(plan.get("tests"), list) or not isinstance(plan.get("results"), dict):
        raise ValueError("The plan requires a tests array and a results object")

    ids = set()
    for test in plan["tests"]:
        fields = ("id", "metric", "unit", "testbench", "analysis", "measurement")
        if not isinstance(test, dict) or not all(text_present(test.get(key)) for key in fields):
            raise ValueError(f"Each test requires text fields: {', '.join(fields)}")
        test_id = test["id"]
        if test.get("role", "acceptance") not in ("acceptance", "diagnostic"):
            raise ValueError(f"{test_id}: role must be acceptance or diagnostic")
        if "not_applicable_reason" in test:
            if (test.get("role") != "diagnostic" or not text_present(test["not_applicable_reason"])
                    or test_id in plan["results"]):
                raise ValueError(f"{test_id}: only an unrun diagnostic may be declared inapplicable, with a reason")
        if test_id in ids:
            raise ValueError(f"Duplicate test ID: {test_id}")
        ids.add(test_id)
        if not isinstance(test.get("conditions"), dict) or not test["conditions"]:
            raise ValueError(f"{test_id}: describe the test conditions")
        check_paths(test.get("inputs", []), f"{test_id}.inputs")
        check_paths(test.get("reference_inputs", []), f"{test_id}.reference_inputs")
        if "recompute" in test:
            validate_recompute(test["recompute"], test["unit"])
        target = test.get("target")
        if target is None:
            continue
        if not isinstance(target, dict) or not text_present(target.get("source")):
            raise ValueError(f"{test_id}: specify the target source")
        limits = target.get("limits")
        if not isinstance(target.get("confirmed"), bool) or not isinstance(limits, dict) or not limits:
            raise ValueError(f"{test_id}: target requires confirmed and numeric limits")
        if any(op not in COMPARISONS or not finite_number(value) for op, value in limits.items()):
            raise ValueError(f"{test_id}: invalid operator or limit")
    if plan["results"].keys() - ids:
        raise ValueError("Every result must reference a test ID in the plan")
    required_sweeps(plan)
    return plan


def file_hash(path, hashes):
    path = path.resolve()
    if path not in hashes:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        hashes[path] = digest.hexdigest()
    return hashes[path]


def record_hash(data):
    record = json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(record.encode("utf-8")).hexdigest()


def snapshot(plan, directory, hashes):
    contexts = {}
    for test in plan["tests"]:
        inputs = dict.fromkeys(plan["inputs"] + test.get("inputs", []))
        references = dict.fromkeys(
            plan.get("reference_inputs", []) + test.get("reference_inputs", [])
        )
        try:
            if not inputs:
                raise ValueError("Declare the design inputs used for this test")
            files = {name: file_hash(directory / name, hashes) for name in inputs}
            reference_files = {name: file_hash(directory / name, hashes) for name in references}
            test_definition = {
                key: value for key, value in test.items() if key not in ("inputs", "reference_inputs")
            }
            # Omitting the default and explicitly spelling it have the same meaning in existing plans.
            if test_definition.get("role") == "acceptance":
                del test_definition["role"]
            reference_hash = record_hash({"test": test_definition, "files": reference_files})
            contexts[test["id"]] = {
                "context_sha256": record_hash({"reference_sha256": reference_hash, "files": files}),
                "reference_sha256": reference_hash if references else None,
                "files": files,
                "reference_files": reference_files,
            }
        except (OSError, RuntimeError, ValueError) as error:
            contexts[test["id"]] = {"error": str(error)}
    return contexts


def review_measurement(test, context, result, directory, hashes):
    if test.get("role") == "diagnostic" and test.get("not_applicable_reason") and result is None:
        return {"status": "NOT_APPLICABLE", "detail": test["not_applicable_reason"]}
    if "error" in context:
        return {"status": "INPUT_UNAVAILABLE", "detail": context["error"]}
    if result is None:
        return {"status": "NOT_RUN"}
    if not isinstance(result, dict):
        return {"status": "INVALID_RESULT"}
    if result.get("context_sha256") != context["context_sha256"]:
        return {"status": "STALE_RESULT"}
    evidence = result.get("evidence")
    if not isinstance(evidence, dict) or not evidence:
        return {"status": "MISSING_EVIDENCE"}
    for name, recorded_hash in evidence.items():
        if not text_present(name):
            return {"status": "INVALID_EVIDENCE_PATH"}
        try:
            actual_hash = file_hash(directory / name, hashes)
        except (OSError, RuntimeError, ValueError):
            return {"status": "MISSING_EVIDENCE", "file": name}
        if actual_hash != recorded_hash:
            return {"status": "CHANGED_EVIDENCE", "file": name}
    if result.get("status") == "run_failed" and text_present(result.get("reason")):
        return {"status": "RUN_FAILED", "detail": result["reason"]}
    value = result.get("value")
    if result.get("status") != "measured" or not finite_number(value) or result.get("unit") != test["unit"]:
        return {"status": "INVALID_MEASUREMENT"}

    replay = {}
    if "recompute" in test:
        spec = test["recompute"]
        if spec["file"] not in evidence:
            return {"status": "MISSING_EVIDENCE", "file": spec["file"], "detail": "Bind the measured spectrum to this result."}
        try:
            computed, record = band_noise_rms(directory / spec["file"], spec, test["unit"])
        except (OSError, csv.Error, ValueError, OverflowError) as error:
            return {"status": "INVALID_SPECTRUM", "file": spec["file"], "detail": str(error)}
        if not math.isclose(computed, value, rel_tol=1e-6):
            return {"status": "MEASUREMENT_MISMATCH", "reported": value, "recomputed": computed,
                    "replay": record, "detail": "Check the selected port, band and ASD versus PSD representation."}
        value = computed
        replay["replay"] = record
    target = test.get("target")
    if test.get("role") == "diagnostic":
        review = {"status": "DIAGNOSTIC_MEASURED", "value": value, **replay}
        if target and target["confirmed"]:
            review["limits"] = target["limits"]
            review["within_limits"] = all(COMPARISONS[op](value, limit) for op, limit in target["limits"].items())
        return review
    if target is None or not target["confirmed"]:
        return {"status": "TARGET_UNCONFIRMED", "value": value, **replay}
    limits = target["limits"]
    passed = all(COMPARISONS[op](value, threshold) for op, threshold in limits.items())
    return {"status": "PASS" if passed else "FAIL", "value": value, "limits": limits, **replay}


def check(plan, directory, contexts, hashes):
    results = []
    for test in plan["tests"]:
        test_id = test["id"]
        review = review_measurement(
            test, contexts[test_id], plan["results"].get(test_id), directory, hashes
        )
        status = review["status"]
        if status != "DIAGNOSTIC_MEASURED":
            review["disposition"] = status if status in ("PASS", "FAIL", "NOT_RUN", "NOT_APPLICABLE") else "BLOCKED"
        if test.get("role") == "diagnostic":
            review["role"] = "diagnostic"
        results.append({
            "id": test_id,
            "metric": test["metric"],
            "unit": test["unit"],
            "reference_sha256": contexts[test_id].get("reference_sha256"),
            **review,
        })
    required = [row for row in results if row.get("role") != "diagnostic"]
    passed = bool(required) and all(test["status"] == "PASS" for test in required)
    report = {
        "status": "RECORDED_LIMITS_MET" if passed else "REVIEW_REQUIRED",
        "tests": results,
        "scope": "Declared input files, evidence digests and recorded measurements.",
    }
    if plan.get("required_sweeps"):
        report["coverage"] = sweep_coverage(plan)
        if any(group["missing"] for group in report["coverage"]):
            report["status"] = "REVIEW_REQUIRED"
    return report


def compare(baseline, candidate):
    base_sweeps, trial_sweeps = (r.get("coverage", []) for r in (baseline, candidate))
    if (any(s["missing"] for s in base_sweeps + trial_sweeps) or
            {s["contract_sha256"] for s in base_sweeps} != {s["contract_sha256"] for s in trial_sweeps}):
        return {"status": "INCOMPARABLE", "passing_variants": [], "candidate_limits_met": False,
                "detail": "Both plans must cover the same required operating conditions."}
    base_tests = {test["id"]: test for test in baseline["tests"] if test.get("role") != "diagnostic"}
    candidate_tests = {test["id"]: test for test in candidate["tests"] if test.get("role") != "diagnostic"}
    if not base_tests or base_tests.keys() != candidate_tests.keys():
        return {"status": "INCOMPARABLE", "passing_variants": [], "candidate_limits_met": False,
                "detail": "The plans must cover the same nonempty set of acceptance tests."}

    comparisons = []
    for test_id, base in base_tests.items():
        trial = candidate_tests[test_id]
        entry = {"id": test_id, "baseline_status": base["status"], "candidate_status": trial["status"]}
        if not base["reference_sha256"] or base["reference_sha256"] != trial["reference_sha256"]:
            entry["status"] = "REFERENCE_MISMATCH"
        elif "value" not in base or "value" not in trial:
            entry["status"] = "MEASUREMENT_UNAVAILABLE"
        else:
            delta = trial["value"] - base["value"]
            entry.update(unit=trial["unit"], baseline=base["value"], candidate=trial["value"])
            if finite_number(delta):
                entry.update(status="COMPARABLE", delta=delta)
            else:
                entry["status"] = "DELTA_OUT_OF_RANGE"
        comparisons.append(entry)
    comparable = all(test["status"] == "COMPARABLE" for test in comparisons)
    passing = [name for name, report in (("baseline", baseline), ("candidate", candidate))
               if comparable and report["status"] == "RECORDED_LIMITS_MET"]
    return {
        "status": "COMPARABLE" if comparable else "INCOMPARABLE",
        "passing_variants": passing,
        "candidate_limits_met": "candidate" in passing,
        "tests": comparisons,
    }


def load_records(path, hashes):
    plan = load_plan(path)
    return plan, snapshot(plan, path.parent, hashes)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["snapshot", "check", "compare"])
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--baseline", type=Path, help="Baseline plan for compare")
    args = parser.parse_args()
    if (args.operation == "compare") != (args.baseline is not None):
        parser.error("Use --baseline with compare")
    try:
        hashes = {}
        plan, contexts = load_records(args.plan, hashes)
        if args.baseline is not None:
            baseline, baseline_contexts = load_records(args.baseline, hashes)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    if args.operation == "snapshot":
        report = {"tests": contexts}
        complete = bool(contexts) and all("error" not in context for context in contexts.values())
        if plan.get("required_sweeps"):
            report["coverage"] = sweep_coverage(plan)
            complete = complete and all(not s["missing"] for s in report["coverage"])
    else:
        records = check(plan, args.plan.parent, contexts, hashes)
        if args.operation == "compare":
            reference_records = check(baseline, args.baseline.parent, baseline_contexts, hashes)
            report = compare(reference_records, records)
            complete = report["status"] == "COMPARABLE"
        else:
            report = records
            complete = report["status"] == "RECORDED_LIMITS_MET"
    print(json.dumps(report, indent=2, allow_nan=False))
    raise SystemExit(0 if complete else 1)


if __name__ == "__main__":
    main()
