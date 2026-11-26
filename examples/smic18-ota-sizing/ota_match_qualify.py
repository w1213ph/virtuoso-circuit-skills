"""Replay nominal performance and local mismatch on the physical candidate frozen by the support tests."""
import math
from pathlib import Path
import re
import runpy
import sys

from ota_cmfb import GATE_AREA_UM2, ICM, joint_grid, performance_at, persist
from ota_robust import fullscale_delivery, load_design
from ota_mismatch import COUNT, SEEDS, coverage, nominal_control, record
from ota_native import audit
from bias_sim import STATES
from spectre_run import read_json, sha256

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"skills/paper-to-virtuoso/scripts"))
from paired_outcomes import compare_passes


HOLDOUT = 152219


def validation_seed(requested, frozen=None):
    if frozen is not None and requested is not None and requested != frozen:
        raise ValueError("The requested validation seed differs from the frozen run")
    if requested is None:
        requested = frozen
    seed = HOLDOUT if requested is None else requested
    if type(seed) is not int or seed <= 0 or seed in SEEDS:
        raise ValueError("Choose a positive validation seed distinct from the two original sample banks")
    return seed


def choose(probes,order):
    for name in order:
        test = probes[name]
        if test["static_passed"]==18 and test["dynamic_passed"]==6:
            return name
    return None


def input_area_estimate(original,larger,gate_before,comparison,tracking_limit_mV):
    """First-order area projection for the isolated pair at fixed W/L and unchanged surrounding devices."""
    w,l = original
    w2,l2 = larger
    if not all(math.isfinite(v) and v>0 for v in (w,l,w2,l2,gate_before,tracking_limit_mV)):
        raise ValueError("Invalid matched-pair dimensions, budget or tracking tolerance")
    a0,a1 = 2*w*l,2*w2*l2
    ceiling = GATE_AREA_UM2-gate_before+a0
    if not math.isclose(w/l,w2/l2) or not a0<a1<=ceiling or gate_before<a0:
        raise ValueError("Area projection requires equal W/L and a larger pair within the fixed gate budget")
    before,after = comparison["before"],comparison["after"]
    peak = max(before["worst_tracking_by_draw_mV"])
    if before["center_sample_sigma_mV"]<=0:
        raise ValueError("The isolated pair needs measurable mismatch before estimating an area ratio")
    return dict(original_pair_um2=a0,larger_pair_um2=a1,maximum_pair_um2_at_fixed_others=ceiling,
                predicted_sigma_ratio=math.sqrt(a0/a1),measured_sigma_ratio=after["center_sample_sigma_mV"]/before["center_sample_sigma_mV"],
                estimated_peak_mV_at_area_ceiling=peak*math.sqrt(a0/ceiling),
                estimated_pair_um2_for_observed_peak=a0*(peak/tracking_limit_mV)**2)


def constant_offset_residual(cases):
    """Subtract each draw's TT, 0.95 V, zero-command error from its measured DC errors, with no circuit changes."""
    total = coverage(cases)["total"]
    spans = []
    references = []
    for i in range(total):
        reference = cases["tt-950"]["samples"][i][1]
        if abs(reference["command_V"])>1e-9:
            raise ValueError("Calibration diagnostic requires the zero-command reference")
        offset = reference["signed_diff_error_mV"]
        errors = [p["signed_diff_error_mV"] for group in cases.values() for p in group["samples"][i]]
        spans.append(max(abs(error-offset) for error in errors))
        references.append(offset)
    return dict(draws=total,reference_offset_range_mV=[min(references),max(references)],
                worst_residual_mV=max(spans),within_1mV_after_arithmetic_subtraction=sum(v<=1 for v in spans))


def joint_verdicts(cases):
    coverage(cases)
    ids = next(iter(cases.values()))["ids"]
    return [(draw,all(not p["failed"] for case in cases.values() for p in case["samples"][i]))
            for i,draw in enumerate(ids)]


def device_curves(out,native,geometries,performance,site,fresh):
    sweep = runpy.run_path(str(Path(__file__).with_name("characterize.py")))["sweep"]
    result = {}
    for state in STATES:
        op = performance[state[0]]["operating_points"]
        for device,kind,nets,path,body in (
                ("MINP","N","XP INP TAIL VSS","XDUT.XI.MINP",op["XDUT.XI.MINP"]["vgs_V"]-.6*state[2]),
                ("MCM","P","PG PG VDD VDD","XDUT.XC.MCM",0.0)):
            width,length = geometries[device]
            card = re.findall(r"(?m)^\s*"+device+r"\s+\("+nets+r"\)\s+("+kind.lower()+r"18 [^\n]+)$",native)
            if len(card)!=1:
                raise ValueError("Cannot uniquely identify native "+device+" on its intended terminals")
            point = op[path]
            key = (length,kind)
            label = state[0]+"-"+device
            nearest,count = sweep(out/label,state,site,{key:card[0]},{key:(point["vds_V"],body)},fresh,
                                  width_um=width,target_current_uA=abs(point["id_uA"]))
            result[label] = dict(points=count,quiescent=point,lookup=nearest[key])
    return result


def paired_samples(folder,source,site,state,icm,seed,arm,fresh):
    label = "{}-{}-{}-{}".format(state[0],round(icm*1000),seed,arm)
    return record(folder/label,source,state,icm,site,arm,seed,fresh)


def qualification(root,ota,cmfb,robust,site,search,order,fresh,holdout):
    from ota_matching import comparison,variant
    folder = root/"qualification"
    if fresh:
        folder.mkdir()
    name = choose(search["probes"],order)
    frozen = dict(chosen=name,source_sha256=sha256(root/"sensitivity.json"),development_seed=SEEDS[0],
                  validation_seed=holdout,selection_rule="First declared geometry passing all 18 DC endpoints and six transients")
    persist(folder/"frozen.json",frozen,fresh)
    if name is None:
        result = dict(selected=None,passed=False,reason="No split passed the static endpoints and both full-scale directions")
        persist(folder/"summary.json",result,fresh)
        return result
    original,sizes,area = load_design(ota,cmfb,site)
    selected = search["probes"][name]
    pairs = selected["geometry"]
    frozen_sizes = dict(sizes,paired_geometry=pairs)
    native,physical = audit(root/name/"oa",site,frozen_sizes)
    if physical!=selected["physical"]:
        raise ValueError("The selected OA differs from the frozen screened geometry")
    stat_sizes = dict(sizes,mos_family="statistical")
    ordinary_stat,base_area = audit(robust/"oa",site,stat_sizes)
    if base_area!=area:
        raise ValueError("The original statistical and ordinary geometry differ")
    statistical,stat_area = variant(folder/"statistical-oa",site,stat_sizes,ordinary_stat,area,pairs,fresh)
    if stat_area!=physical:
        raise ValueError("The candidate statistical and ordinary geometry differ")
    corners = {s[0]:performance_at(folder,name,native,site,s,physical,fresh) for s in STATES}
    grid = joint_grid(folder,native,site,fresh)
    rows = [r for group in grid.values() for r in group]
    dynamic,maestro = fullscale_delivery(folder,native,frozen_sizes,physical,site,fresh)
    if any({k:v for k,v in r.items() if k!="raw_sha256"}!={k:v for k,v in selected["fullscale"][label].items() if k!="raw_sha256"}
           for label,r in dynamic.items()):
        raise ValueError("The sized physical testbench does not reproduce its full-scale screen")
    quiescent = {k:r["metrics"] for k,r in corners.items()}
    base_corners = read_json(cmfb/"midpoint/summary.json")["candidate"]["corners"]
    before_sizes = {"MINP":(sizes["input"],sizes["length_um"]),"MCM":(sizes["load"],sizes["length_um"])}
    after_sizes = {"MINP":pairs.get("input_pair",before_sizes["MINP"]),"MCM":pairs.get("cm_loads",before_sizes["MCM"])}
    curves = {}
    for label,net,geometry,op in (("before",original,before_sizes,{k:r["metrics"] for k,r in base_corners.items()}),
                                  ("after",native,after_sizes,quiescent)):
        path = folder/(label+"-devices")
        if fresh:
            path.mkdir()
        curves[label] = device_curves(path,net,geometry,op,site,fresh)
    old = read_json(robust/"summary.json")["cases"]
    batches,controls = {},{}
    tested = {}
    residuals = {}
    for seed in (SEEDS[0],holdout):
        prior,current = {},{}
        for state in STATES:
            for icm in ICM:
                key = "{}-{}".format(state[0],round(1000*icm))
                if seed==SEEDS[0]:
                    controls[key] = nominal_control(folder,site,state,icm,native,statistical,fresh)
                    prior[key] = old[str(seed)][key]
                else:
                    prior[key] = paired_samples(folder,ordinary_stat,site,state,icm,seed,"all",fresh)
                # Keep original and candidate raw results in separate directories, even when the seed matches.
                path = folder/("candidate-"+str(seed))
                if fresh:
                    path.mkdir(exist_ok=True)
                current[key] = paired_samples(path,statistical,site,state,icm,seed,"all",fresh)
        batches[str(seed)] = comparison(prior,current)
        before_draws = joint_verdicts(prior)
        after_draws = joint_verdicts(current)
        changes = compare_passes(before_draws,after_draws)
        print("Paired joint decisions",seed,changes,flush=True)
        after_by_id = dict(after_draws)
        for i,(draw,was_passing) in enumerate(before_draws,1):
            if was_passing != after_by_id[draw]:
                print("Seed {} native draw {:03d}: {}".format(seed,i,"lost pass" if was_passing else "gained pass"),flush=True)
        residuals[str(seed)] = dict(before=constant_offset_residual(prior),after=constant_offset_residual(current))
        tested[seed] = set(current["tt-950"]["ids"])
    used = {v for cases in old.values() for v in cases["tt-950"]["ids"]}
    if len(tested[holdout])!=COUNT or tested[holdout] & (used|tested[SEEDS[0]]):
        raise ValueError("The validation set overlaps previous development samples")
    grid_passed = sum(r["passed"] for r in rows)
    gates = dict(nominal=all(r["passed"] for r in corners.values()),joint_grid=len(rows)==693 and grid_passed==693,
                 fullscale=all(r["passed"] for r in dynamic.values()),
                 development=batches[str(SEEDS[0])]["after"]["joint"]["passed"]==COUNT,
                 independent=batches[str(holdout)]["after"]["joint"]["passed"]==COUNT)
    pair_probe = search["probes"]["pair"]
    estimate = input_area_estimate(before_sizes["MINP"],pair_probe["geometry"]["input_pair"],area["area"]["mos_gate_um2"],
                                   pair_probe["paired"]["input_pair"],1.0)
    result = dict(selected=name,frozen_sha256=sha256(folder/"frozen.json"),physical=physical,corners=corners,grid=grid,
                  fullscale=dynamic,maestro=maestro,device_curves=curves,grid_passed=grid_passed,
                  nominal_controls=controls,paired=batches,isolated_pair_area_estimate=estimate,
                  fixed_offset_diagnostic=residuals,checks=gates,passed=all(gates.values()))
    persist(folder/"summary.json",result,fresh)
    print("Frozen geometry:",name,"grid",grid_passed,"/693; hard gates:",gates,flush=True)
    for seed,r in batches.items():
        print("Seed",seed,"before",r["before"]["joint"],"after",r["after"]["joint"],flush=True)
    return result
