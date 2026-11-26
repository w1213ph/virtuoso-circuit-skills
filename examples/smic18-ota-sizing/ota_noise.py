"""Measure integrated differential input noise and re-rank an existing physical OTA search."""
import argparse
import csv
import io
import json
import math
import re
import sys
from pathlib import Path

from ota import candidates, config_for_site
from ota_measure import LIMITS, deck, measure, samples
from ota_native import audit
from ota_views import TB
from bias_sim import STATES, analysis
from spectre_run import read_json, save_json, sha256

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "skills/paper-to-virtuoso/scripts"))
import check_prelayout as acceptance

NOISE = "ns (OUTP OUTN) noise start=1 stop=1G dec=60 iprobe=VD"
GROUPS = ("input_pair", "input_load", "tail", "output_stage", "bias", "cmfb")
TRIM_GROUPS = GROUPS+("trim_resistors","trim_switches","trim_inverters")
COLUMNS = ("frequency_Hz", "input_V_per_sqrt_Hz", "output_V_per_sqrt_Hz", "diff_gain_V_per_V")


def group_for(instance):
    if instance in ("XDUT.XI.MINP", "XDUT.XI.MINN"):
        return "input_pair"
    if instance in ("XDUT.XI.MLP", "XDUT.XI.MLN"):
        return "input_load"
    if instance == "XDUT.XI.MTAIL":
        return "tail"
    for pattern,group in ((r"MSW[PN][0-3]","trim_switches"),(r"MIV[PN][0-3]","trim_inverters"),
                          (r"RS[PN][0-3]\.\w+","trim_resistors")):
        if re.fullmatch(r"XDUT\.XI\."+pattern,instance):
            return group
    for prefix, group in (("XDUT.XO.", "output_stage"), ("XDUT.XB.", "bias"), ("XDUT.XC.", "cmfb")):
        if instance.startswith(prefix):
            return group
    raise ValueError("Unaccounted native noise source " + instance)


def spectrum(noise_path, ac_path, groups=GROUPS):
    """Read aggregate ASD and per-instance output PSD from the same native noise solution."""
    header, values = noise_path.read_text(encoding="utf8").split("\nVALUE\n")
    for field in ('"positive output signal" "OUTP"', '"negative output signal" "OUTN"', '"input probe" "VD"',
                  '"analysis type" "noise"', '"out" "V/sqrt(Hz)"', '"in" "V/sqrt(Hz)"', '"gain" "V/V"'):
        if field not in header.splitlines():
            raise ValueError("Incorrect native probe, output pair or units: " + field)
    if not values.rstrip().endswith("END"):
        raise ValueError("Incomplete noise analysis")
    definitions = dict(re.findall(r'^"([^"]+)" STRUCT\(\n(.*?)^\) PROP\(', header, re.M | re.S))
    sources = dict(re.findall(r'^"(XDUT\.[^"]+)" "([^"]+)"$', header.split("\nTRACE\n")[1], re.M))
    for kind in set(sources.values()):
        fields = re.findall(r'^"([^"]+)" FLOAT DOUBLE PROP\(\n"units" "([^"]+)"', definitions[kind], re.M)
        if not fields or fields[-1] != ("total", "V^2/Hz"):
            raise ValueError("Undeclared device noise power or units for " + kind)
    native_groups = {name:group_for(name) for name in sources}
    if set(native_groups.values()) != set(groups):
        raise ValueError("Noise analysis omitted a physical functional block")
    ac = samples(ac_path, "freq", ("INP", "INN", "OUTP", "OUTN"))
    points = re.split(r'^"freq" ', values, flags=re.M)[1:]
    if len(points) != len(ac["freq"]):
        raise ValueError("AC and noise sweeps have different lengths")
    rows = []
    for i, point in enumerate(points):
        freq = float(point.splitlines()[0])
        signals = re.findall(r'^"(out|in|gain)" ([^\n]+)$', point, re.M)
        if len(signals) != 3 or {k for k,_ in signals} != {"out","in","gain"}:
            raise ValueError("Missing or repeated native noise signal")
        sig = {k:float(v) for k,v in signals}
        stimulus = ac["INP"][i] - ac["INN"][i]
        diff_gain = abs(ac["OUTP"][i] - ac["OUTN"][i])
        if (abs(stimulus-1) > 1e-6 or not math.isclose(freq, ac["freq"][i], rel_tol=1e-7)
                or not all(math.isfinite(v) and v > 0 for v in sig.values())
                or not math.isclose(sig["gain"], diff_gain, rel_tol=1e-4)
                or not math.isclose(sig["in"] * diff_gain, sig["out"], rel_tol=1e-4)):
            raise ValueError("Differential AC gain disagrees with the input or output noise at {} Hz".format(freq))
        parts = re.findall(r'^"(XDUT\.[^"]+)" \(\n(.*?)^\)', point, re.M | re.S)
        if len(parts) != len(sources) or {n for n,_ in parts} != set(sources):
            raise ValueError("Missing or repeated device noise contribution")
        power = dict.fromkeys(groups, 0.0)
        for instance, terms in parts:
            total = float(terms.splitlines()[-1])
            if not math.isfinite(total) or total < 0:
                raise ValueError("Invalid device total noise power")
            power[native_groups[instance]] += total
        if not math.isclose(sum(power.values()), sig["out"] ** 2, rel_tol=1e-5):
            raise ValueError("The sum of physical source powers disagrees with output noise")
        rows.append((freq, sig["in"], sig["out"], diff_gain, *(power[g]/diff_gain**2 for g in groups)))
    return rows


def store(path, data, fresh):
    if fresh:
        path.write_bytes(data)
    elif path.read_bytes() != data:
        raise ValueError("Saved evidence differs from independently replayed native results: " + str(path))


def export_csv(path, rows, fresh, groups=GROUPS):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow((*COLUMNS, *(key + "_V2_per_Hz" for key in groups)))
    writer.writerows(rows)
    store(path, stream.getvalue().encode("utf8"), fresh)


def noise_test(name, state, band, limit, csv_name):
    return dict(id=name+"-"+state[0], metric="input_referred_noise", unit="uV RMS", testbench=TB,
                analysis="Spectre noise", measurement="One-sided differential input voltage RMS",
                conditions=dict(candidate=name, corner=state[0], temperature_C=state[1], supply_V=state[2],
                                load_pF_each=2, input_cm_fraction_VDD=0.6, differential_feedback="open", cmfb="closed"),
                recompute=dict(method="band_noise_rms", file=csv_name, axis=COLUMNS[0], density=COLUMNS[1],
                               density_unit="V/sqrt(Hz)", band_Hz=band),
                target=dict(source="Explicit example or caller noise budget", confirmed=True, limits={"<=":limit}))


def assess(path, test, groups=GROUPS):
    spec = test["recompute"]
    rms, _ = acceptance.band_noise_rms(path, spec, test["unit"])
    fractions = {}
    for group in groups:
        group_spec = dict(spec, density=group+"_V2_per_Hz", density_unit="V2/Hz")
        contribution, _ = acceptance.band_noise_rms(path, group_spec, test["unit"])
        fractions[group] = (contribution/rms)**2
    if not math.isclose(sum(fractions.values()), 1, rel_tol=3e-4):
        raise ValueError("Integrated contribution powers disagree with the input spectrum")
    return rms, fractions


def evaluate(ota_dir, out, site, band, limit, fresh):
    original = read_json(ota_dir/"summary.json")
    names = [name for name,_ in candidates()]
    if (original["model_sha256"] != site["model_sha256"] or original["pdk_revision"] != site["pdk_revision"]
            or original["pdk_library"] != site["pdk_library"] or original["limits"] != LIMITS
            or set(original["candidates"]) != set(names)):
        raise ValueError("Current PDK or original design search differs from the native OTA baseline")
    portable = out/"portable"
    if fresh:
        out.mkdir(parents=True, exist_ok=False)
        portable.mkdir()
    plan = dict(schema_version=1, inputs=["design.json"], tests=[], results={},
                required_sweeps=[dict(metric="input_referred_noise",axes=dict(candidate=names,corner=[s[0] for s in STATES]))])
    reports, native_hashes, measured = {}, {}, {}
    for name in names:
        base = ota_dir/name
        previous = original["candidates"][name]
        native, physical = audit(base/"oa", site, previous["sizes"])
        if physical != previous["physical"]:
            raise ValueError("Physical devices, areas or ports changed for " + name)
        corners = {}
        for state in STATES:
            corner = state[0]
            orig = base/corner
            baseline = measure(orig, site, state, native, False)
            if baseline != previous["corners"][corner]:
                raise ValueError("Native baseline differs for "+name+"/"+corner)
            gain_deck = deck(site, state, native, "gain")
            ac_command = "ac ac start=1 stop=1G dec=60"
            if gain_deck.count(ac_command) != 1:
                raise ValueError("Cannot identify the native gain testbench")
            native_dir = out/(name+"-"+corner)
            raw = analysis(native_dir, gain_deck.replace(ac_command,NOISE), "ns.noise", site, site["spectre"] if fresh else None)
            job = read_json(native_dir/"job.json")
            if job["warnings"] or any(sha256(Path(k)) != v for k,v in job["dependencies"].items()):
                raise ValueError("Review Spectre warnings or changed input files")
            ac_job = read_json(orig/"gain/job.json")
            rows = spectrum(raw, orig/"gain/psf"/ac_job["trace"])
            path = portable/(name+"-"+corner+".csv")
            export_csv(path, rows, fresh)
            test = noise_test(name, state, band, limit, path.name)
            rms, fractions = assess(path, test)
            plan["tests"].append(test)
            measured[test["id"]] = rms
            native_hashes[test["id"]] = dict(ac=ac_job["raw_sha256"],noise=job["raw_sha256"],deck=job["deck_sha256"])
            corners[corner] = dict(input_noise_uVrms=rms, power_uW=baseline["power_uW"], noise_power_fraction=fractions,
                                   original_checks=baseline["checks"], total_checks_pass=baseline["passed"] and rms <= limit)
        reports[name] = dict(corners=corners, area_um2=physical["area"], worst_noise_uVrms=max(v["input_noise_uVrms"] for v in corners.values()),
                             worst_power_uW=max(v["power_uW"] for v in corners.values()), passed=all(v["total_checks_pass"] for v in corners.values()))
        print(name,"worst noise {:.3f} uV RMS".format(reports[name]["worst_noise_uVrms"]),flush=True)
    fingerprint = dict(native_traces=native_hashes,original_design_sha256=sha256(ota_dir/"summary.json"),pdk_revision=site["pdk_revision"])
    store(portable/"design.json", (json.dumps(fingerprint,sort_keys=True,indent=2)+"\n").encode(), fresh)
    hashes = {}
    contexts = acceptance.snapshot(plan, portable, hashes)
    for test in plan["tests"]:
        path = test["recompute"]["file"]
        plan["results"][test["id"]] = dict(status="measured",value=measured[test["id"]],unit=test["unit"],
             evidence={path:acceptance.file_hash(portable/path,hashes)},context_sha256=contexts[test["id"]]["context_sha256"])
    replay = acceptance.check(plan, portable, contexts, hashes)
    if any(row["status"] not in ("PASS","FAIL") for row in replay["tests"]) or any(s["missing"] for s in replay["coverage"]):
        raise ValueError("Portable spectrum could not reproduce all circuit measurements")
    passing = sorted((v["worst_power_uW"],sum(v["area_um2"].values()),n) for n,v in reports.items() if v["passed"])
    report = dict(band_Hz=band, limit_uVrms=limit, candidates=reports, selected=passing[0][2] if passing else None,
                  previous_selected=original["selected"], native_noise_runs=len(native_hashes), original_analyses_replayed=5*len(native_hashes))
    return plan, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run","verify","evaluate"))
    for key in ("config","ota","out"):
        parser.add_argument("--"+key, type=Path, required=True)
    parser.add_argument("--band-hz", type=float, nargs=2, metavar=("LOW","HIGH"))
    parser.add_argument("--limit-uv", type=float)
    args = parser.parse_args()
    new = args.action=="run"
    out = args.out.expanduser().resolve()
    saved = {} if new else read_json(out/"summary.json")
    band = args.band_hz or saved.get("band_Hz", [10.0, 100000.0])
    limit = saved.get("limit_uVrms",60.0) if args.limit_uv is None else args.limit_uv
    if (not all(math.isfinite(x) for x in (*band,limit)) or not 1 <= band[0] < band[1] <= 1e9 or limit<=0):
        raise ValueError("Specify a band within the native 1 Hz to 1 GHz sweep and a positive RMS voltage limit")
    if args.action=="verify" and (band!=saved["band_Hz"] or limit!=saved["limit_uVrms"]):
        raise ValueError("Use evaluate to review a different specification")
    site = config_for_site(args.config.expanduser().resolve())
    site["model_file"] = str(site["model_file"])
    site["model_sha256"] = sha256(Path(site["model_file"]))
    plan, report = evaluate(args.ota.expanduser().resolve(),out,site,band,limit,new)
    if new:
        save_json(out/"portable/plan.json", plan)
        save_json(out/"summary.json",report)
    elif args.action=="verify" and (report!=saved or plan!=read_json(out/"portable/plan.json")):
        raise ValueError("Summary or acceptance plan differs from native replay")
    print(json.dumps({k:v for k,v in report.items() if k!="candidates"},indent=2))
    return 0 if report["selected"] else 1


if __name__=="__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, OverflowError, RuntimeError, KeyError, TypeError, csv.Error) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)
