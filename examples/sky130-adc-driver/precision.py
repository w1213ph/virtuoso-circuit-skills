"""Compare mirror ratios and switch sizes while keeping the ADC interface fixed."""
import copy

from bench import deck
from driver import OTA, SAMPLER
from mos_table import table


ORIGINALS = ("baseline", "candidate")
FAMILIES = {"short": "baseline", "long": "candidate"}
RATIOS = (("equal", 0), ("minus2", -2), ("minus1", -1), ("plus1", 1), ("plus2", 2))
DC_VECTORS = ("v(vin)", "v(out)", "v(sample)", "v(clk)", "v(clkb)", "v(vdd)")


def dc_deck(native, library, state, req):
    circuit = deck(native, library, state, req, True).split(".control\n", 1)[0]
    low, high = req["input_low_V"], req["input_high_V"]
    return circuit + "\n".join((
        ".control", "set wr_singlescale", "set wr_vecnames", "set numdgt=12",
        "dc VDATA {:.14g} {:.14g} {:.14g}".format(low, high, (high - low) / 8),
        "wrdata dc.dat " + " ".join(DC_VECTORS), "echo analysis_complete", "quit", ".endc", ".end", "",
    ))


def dc_transfer(folder, req, vdd):
    rows = table(folder / "dc.dat", DC_VECTORS)
    if len(rows) != 9:
        raise ValueError("Expected nine static input levels at the sampling capacitor")
    low, high = req["input_low_V"], req["input_high_V"]
    points = []
    for i, (sweep, vin, out, cap, clk, clkb, rail) in enumerate(rows):
        target = low + i * (high - low) / 8
        if max(abs(sweep-target), abs(vin-target), abs(rail-vdd), abs(clk-vdd), abs(clkb)) > 1e-8:
            raise ValueError("DC input, supply or tracking clock differs at level " + str(i))
        points.append({"input_V": vin, "out_V": out, "sample_V": cap, "signed_error_mV": (cap-vin)*1000})
    worst = max(points, key=lambda p: abs(p["signed_error_mV"]))
    return {"points": points, "worst_input_V": worst["input_V"], "worst_abs_mV": abs(worst["signed_error_mV"])}


def modified(base, *, extra=None, switch=1):
    result = copy.deepcopy(base)
    devices = result["devices"]
    if extra is not None:
        w, length, count = devices["XLA"]
        # The diode branch keeps its total W/L. The output branch changes in identical unit devices.
        devices["XLA"] = (w/4, length, 4*count)
        devices["XLB"] = (w/4, length, 4*count + extra)
    for name in SAMPLER:
        w, length, count = devices[name]
        devices[name] = (w, length, count * switch)
    for key, members in (("core_gate_area_um2", OTA), ("switch_gate_area_um2", SAMPLER)):
        result[key] = sum(w * length * count for w, length, count in (devices[name] for name in members))
    return result


def evaluate_family(originals, optimize, measure):
    """measure returns independently checkable values for one complete native schematic."""
    records = {name: measure(name, originals[name]) for name in ORIGINALS}
    if not optimize or any(row["passed"] for row in records.values()):
        return records
    # Compare the equal split and both correction polarities in each gm/ID-sized family.
    for family, parent in FAMILIES.items():
        cores = {}
        for label, extra in RATIOS:
            name = family + "-" + label
            cores[name] = modified(originals[parent], extra=extra)
            records[name] = measure(name, cores[name])
        best = min(cores, key=lambda name: (records[name]["static_mV"], records[name]["gate_um2"], name))
        for factor in (2, 3):
            name = best + "-tg" + str(factor)
            records[name] = measure(name, modified(cores[best], switch=factor))
    # Change only the switch on the original long-channel driver to isolate its contribution.
    records["candidate-tg2"] = measure("candidate-tg2", modified(originals["candidate"], switch=2))
    return records


def same_fixture(sections, fixture, resized_switch):
    if sections["TOP"] != fixture["TOP"]:
        return False
    actual, standard = sections["ADRV_SAMPLER"], fixture["ADRV_SAMPLER"]
    if not resized_switch:
        return actual == standard
    # The native audit separately checks W/L, multiplicity and every pin on both transistors.
    return (actual.keys() == standard.keys() and actual["CSH"] == standard["CSH"]
            and all(actual[n][:-1] == standard[n][:-1] for n in SAMPLER))


def summarize(sizing, measurements, review):
    valid = ("PASS", "FAIL", "DIAGNOSTIC_MEASURED")
    if any(test["status"] not in valid for test in review["tests"]):
        raise ValueError("Incomplete precision comparison: inspect checked.json before selecting a circuit")
    static = [(m["dc"]["worst_abs_mV"], c, m["dc"]["worst_input_V"]) for c, m in measurements.items()]
    worst, corner, vin = max(static)
    count = sum(test["status"] == "PASS" for test in review["tests"])
    if sum(test.get("role") != "diagnostic" for test in review["tests"]) != 18:
        raise ValueError("All 18 acquisition and stability measurements are required")
    return {
        "passed": count == 18, "pass_count": count, "static_mV": round(worst, 8),
        "static_corner": corner, "static_input_V": vin,
        "gate_um2": round(sizing["core_gate_area_um2"] + sizing["switch_gate_area_um2"], 8),
    }


def selection(records):
    passing = [name for name, result in records.items() if result["passed"]]
    selected = min(passing, key=lambda name: (records[name]["gate_um2"], name)) if passing else None
    return passing, selected
