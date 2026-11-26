"""Isolate differential feedthrough in the OTA common-mode detector and test physical repairs."""
import argparse
import math
from pathlib import Path
import re
import runpy
import shlex
import sys

from ota import config_for_site
import ota_ade
from ota_measure import LIMITS, NODES, OP_DEVICES, OP_SAVES, deck, measure, samples
from ota_native import audit, build, hierarchy
from ota_noise import NOISE, assess, export_csv, noise_test, spectrum
from ota_range import STEP_COUNT, covers, measure_ranges, replace_fixture_field
from ota_views import CMFB, PROBED
from bias_sim import STATES, analysis
from spectre_run import read_json, save_json, sha256


BASE = "l150-i06-c275"
GEOMETRIES = ((0.90,1.5), (0.65,1.5), (0.45,1.5), (0.45,2.25), (0.45,3.0), (0.45,4.5))
ICM = (0.95, 1.10)
SWING = (-0.30, 0.30)
POWER_UW = 25.22
GATE_AREA_UM2 = 114.19
NOISE_UV = 40.0
BAND = [10, 100000]


def averaged_detector(native):
    """Replace only the two feedback-sense connections, keeping the original finite-gain loop."""
    definitions, _ = hierarchy(native)
    pins, _ = definitions[CMFB]
    nodes, cell, fields = definitions[PROBED][1]["XC"]
    if (cell != CMFB or fields or len(pins) != len(nodes) or len(set(pins)) != len(pins)
            or {p:n for p,n in zip(pins,nodes) if p in ("OUTP","OUTN")} != {"OUTP":"OUTP","OUTN":"OUTN"}
            or "CMAVG" in native or "EAVG" in native):
        raise ValueError("Expected the original, independently wired detector inputs")
    nets = ["CMAVG" if p in ("OUTP","OUTN") else n for p,n in zip(pins,nodes)]
    pattern = r"(?m)^([ \t]*XC[ \t]+)\("+r"\s+".join(map(re.escape,nodes))+r"\)([ \t]+"+CMFB+r")[ \t]*$"
    added = "\nEAVG1 (CMAVG_HALF VSS OUTP VSS) vcvs gain=0.5\nEAVG2 (CMAVG CMAVG_HALF OUTN VSS) vcvs gain=0.5"
    changed, count = re.subn(pattern,lambda m:m[1]+"("+" ".join(nets)+")"+m[2]+added,native)
    if count != 1:
        raise ValueError("Cannot uniquely identify the CMFB instance in the loaded OTA")
    return changed


def checked_trace(folder, source, filename, site, fresh):
    raw = analysis(folder, source, filename, site, site["spectre"] if fresh else None)
    job = read_json(folder/"job.json")
    if job["warnings"] or any(sha256(Path(k)) != v for k,v in job["dependencies"].items()):
        raise ValueError("Review simulation warnings or changed input files in " + str(folder))
    return raw


def swing_source(native, state, site, icm, points, averaged, reverse):
    if icm is not None:
        native = replace_fixture_field(native,"EVIC","gain","0.6","({:.15g}/VDDVAL)".format(icm))
    native = replace_fixture_field(native,"VD","dc","0","(-CMCOMMAND)" if reverse else "CMCOMMAND")
    if points not in (3,21,81):
        raise ValueError("Unsupported differential sweep resolution")
    step = (SWING[1]-SWING[0])/(points-1)
    source = deck(site,state,native,"dc")
    signals = ("XDUT.CMG",) + (("XDUT.CMAVG",) if averaged else ())
    before = "\ndc dc\n"
    if source.count(before) != 1:
        raise ValueError("Expected one closed-loop DC analysis")
    control = "\nparameters CMCOMMAND=0\nsave "+" ".join(signals)+"\n"
    return source.replace(before,control+"cm_sweep dc param=CMCOMMAND start=-0.3 stop=0.3 step={:g}\n".format(step))


def swing_points(raw, state, icm, points=3, averaged=False, reverse=False):
    supply = state[2]
    step = (SWING[1]-SWING[0])/(points-1)
    signals = ("XDUT.CMG",) + (("XDUT.CMAVG",) if averaged else ())
    data = samples(raw,"CMCOMMAND",NODES+OP_SAVES+signals)
    if len(data["CMCOMMAND"]) != points:
        raise ValueError("Incomplete closed-loop differential sweep")
    rows = []
    for i,axis in enumerate(data["CMCOMMAND"]):
        x = -axis if reverse else axis
        op,on,ip,inn = (data[k][i] for k in ("OUTP","OUTN","INP","INN"))
        outcm = (op+on)/2
        if max(abs(axis-(SWING[0]+step*i)),abs(data["CMD"][i]-x),abs(data["VDD"][i]-supply),
               abs(data["VCM"][i]-supply/2),abs((ip+inn)/2-(.6*supply if icm is None else icm)),
               abs(ip-inn-(x-(op-on))),abs(data["XDUT.CMAVG"][i]-outcm) if averaged else 0) > 1e-7:
            raise ValueError("Unexpected command, loop connection, input common-mode or sensing voltage")
        margins = {name:abs(data[name+":vds"][i])-abs(data[name+":vdsat"][i]) for name in OP_DEVICES}
        limiting = min(margins,key=margins.get)
        current = {name:1e6*data["XDUT.XC."+name+":id"][i] for name in
                   ("MDETP","MDETN","MCREFP","MCREFN","MCTP","MCTN","MCM","MDMY")}
        err = 1000*(outcm-supply/2)
        tracking = 1000*abs(op-on-x)
        power = -1e6*data["VSUP:p"][i]*supply
        rows.append(dict(command_V=x,output_p_V=op,output_n_V=on,cm_error_mV=err,tracking_mV=tracking,
                         headroom_V=margins[limiting],limiting_device=limiting,control_V=data["XDUT.CMG"][i],
                         current_uA=current,power_uW=power,passed=abs(err)<=LIMITS["cm_error_mV"] and tracking<=1
                         and margins[limiting]>=0 and 0<=min(op,on)<=max(op,on)<=supply and power>0))
    return list(reversed(rows)) if reverse else rows


def exact_swing(folder, native, state, site, fresh, icm=None, points=3, averaged=False, reverse=False):
    source = swing_source(native,state,site,icm,points,averaged,reverse)
    raw = checked_trace(folder,source,"cm_sweep.dc",site,fresh)
    return swing_points(raw,state,icm,points,averaged,reverse)


def parent(ota,site):
    original = read_json(ota/"summary.json")
    if any(original[k]!=site[k] for k in ("pdk_library","pdk_revision","model_sha256")) or original["limits"]!=LIMITS:
        raise ValueError("The saved design and the current PDK or baseline requirements differ")
    ref = original["candidates"][BASE]
    native, physical = audit(ota/BASE/"oa",site,ref["sizes"])
    if physical!=ref["physical"] or not math.isclose(ref["sizes"]["input"],1.3) or "cm_input" in ref["sizes"]:
        raise ValueError("The reference geometry or passives differ from the original search")
    return native, ref


def check_zero_command(first,second):
    if (abs(first["command_V"])>1e-9 or abs(second["command_V"])>1e-9 or
            any(abs(first[k]-second[k])>1e-6 for k in ("output_p_V","output_n_V","control_V")) or
            first["current_uA"].keys()!=second["current_uA"].keys() or
            any(abs(v-second["current_uA"][k])>1e-5 for k,v in first["current_uA"].items())):
        raise ValueError("The diagnostic fixtures disagree at the symmetric quiescent point")


def diagnose(folder,native,site,fresh):
    if fresh:
        folder.mkdir()
    result = {}
    for state in STATES:
        name = state[0]
        cases = {}
        for averaged in (False,True):
            label = "ideal_average" if averaged else "physical"
            circuit = averaged_detector(native) if averaged else native
            rows = exact_swing(folder/(name+"-"+label),circuit,state,site,fresh,points=81,averaged=averaged)
            cases[label] = [rows[i] for i in (0,7,10,40,70,73,80)]
        check_zero_command(cases["physical"][3],cases["ideal_average"][3])
        result[name] = cases
    return result


def requested_range(coarse, supply, forward, reverse):
    # Demand every known coarse point inside the specification and both directions right up to its exact endpoints.
    grid = [supply*(2*i/STEP_COUNT["swing"]-1) for i in range(STEP_COUNT["swing"]+1)]
    interior = [v for v in grid if SWING[0]-1e-9<=v<=SWING[1]+1e-9]
    if len(forward)!=81 or len(reverse)!=81:
        raise ValueError("Incomplete fine DC range")
    largest_difference = 0
    for i,(a,b) in enumerate(zip(forward,reverse)):
        x = SWING[0]+(SWING[1]-SWING[0])*i/80
        if max(abs(a["command_V"]-x),abs(b["command_V"]-x))>1e-8:
            raise ValueError("Misaligned forward and reverse commands")
        largest_difference = max(largest_difference, *(abs(a[k]-b[k])*1000 for k in ("output_p_V","output_n_V")))
    rows = forward+reverse
    connected = covers(coarse,[interior[0],interior[-1]])
    return dict(passed=connected and all(r["passed"] for r in rows) and largest_difference<=0.5,
                coarse_covers_both_endpoints=covers(coarse,SWING),all_interior_coarse_points_connected=connected,
                points_each_direction=len(forward),step_mV=7.5,largest_direction_difference_mV=largest_difference,
                worst_abs_cm_error_mV=max(abs(r["cm_error_mV"]) for r in rows),
                worst_tracking_mV=max(r["tracking_mV"] for r in rows),min_headroom_V=min(r["headroom_V"] for r in rows))


def performance_at(folder,name,native,site,state,physical,fresh):
    corner = state[0]
    metrics = measure(folder/corner,site,state,native,fresh)
    bounds = measure_ranges(folder,native,site,state,metrics,fresh)
    forward = exact_swing(folder/(corner+"-fine-up"),native,state,site,fresh,points=81)
    reverse = exact_swing(folder/(corner+"-fine-down"),native,state,site,fresh,points=81,reverse=True)
    target = requested_range(bounds["output_diff"]["bounds_V"],state[2],forward,reverse)
    gain_deck = deck(site,state,native,"gain")
    gain_line = "ac ac start=1 stop=1G dec=60"
    if gain_deck.count(gain_line)!=1:
        raise ValueError("Missing the matching open-differential AC bench")
    raw = checked_trace(folder/(corner+"-noise"),gain_deck.replace(gain_line,NOISE),"ns.noise",site,fresh)
    rows = spectrum(raw,folder/corner/"gain/psf/ac.ac")
    path = folder/(corner+"-noise.csv")
    export_csv(path,rows,fresh)
    rms,_ = assess(path,noise_test(name,state,BAND,NOISE_UV,path.name))
    checks = dict(metrics["checks"], input_common=covers(bounds["input_cm"]["bounds_V"],ICM),
                  differential_swing=target["passed"],
                  noise=rms<=NOISE_UV, power=metrics["power_uW"]<=POWER_UW,
                  area=physical["area"]["mos_gate_um2"]<=GATE_AREA_UM2)
    return dict(checks=checks,passed=all(checks.values()),input_common_V=bounds["input_cm"]["bounds_V"],
                coarse_output_diff_V=bounds["output_diff"]["bounds_V"],requested_swing=target,noise_uVrms=rms,noise_csv_sha256=sha256(path),
                dc_sweep_csv_sha256=bounds["points_sha256"],metrics=metrics)


def device_curve(folder,native,site,state,width,length,baseline,fresh):
    # The replica uses the native drawn MOS including diffusion geometry, at the parent reference branch bias.
    device = re.findall(r"(?m)^\s*MCREFP\s+\(RCG VCM TCP VSS\)\s+(n18 [^\n]+)$",native)
    if len(device)!=1:
        raise ValueError("Cannot find the unique reference device on the correct four terminals")
    point = baseline["operating_points"]["XDUT.XC.MCREFP"]
    vbs = point["vgs_V"]-state[2]/2
    key = (length,"N")
    sweep = runpy.run_path(str(Path(__file__).with_name("characterize.py")))["sweep"]
    values,count = sweep(folder,state,site,{key:device[0]},{key:(point["vds_V"],vbs)},fresh,
                         width_um=width,target_current_uA=abs(point["id_uA"]))
    return dict(points=count,reference_current_uA=abs(point["id_uA"]),nearest=values[key])


def physical_trial(out,width,length,site,ref,fresh):
    name = "w{:03d}-l{:03d}".format(round(width*100),round(length*100))
    folder = out/name
    sizes = dict(ref["sizes"],cm_input=width,cm_length_um=length)
    if fresh:
        folder.mkdir()
        build(folder/"oa",site,sizes)
    native,physical = audit(folder/"oa",site,sizes)
    expected_area = ref["physical"]["area"]["mos_gate_um2"]+4*(width*length-sizes["input"]*sizes["length_um"])
    if (not math.isclose(physical["area"]["mos_gate_um2"],expected_area,abs_tol=1e-6)
            or physical["passives"]!=ref["physical"]["passives"]):
        raise ValueError("Physical change exceeds the four matched detector devices")
    curves = {s[0]:device_curve(folder/("device-"+s[0]),native,site,s,width,length,ref["corners"][s[0]],fresh)
              for s in STATES}
    endpoints = {s[0]:exact_swing(folder/("screen-"+s[0]),native,s,site,fresh) for s in STATES}
    screen = all(row["passed"] for rows in endpoints.values() for row in rows)
    corners = {}
    if screen:
        corners = {s[0]:performance_at(folder,name,native,site,s,physical,fresh) for s in STATES}
    accepted = screen and all(c["passed"] for c in corners.values())
    record = dict(width_um=width,length_um=length,device_curves=curves,physical=physical,
                  screen=endpoints,corners=corners,passed=accepted)
    print(name,"CM error at +0.30 V (mV)",[round(v[-1]["cm_error_mV"],3) for v in endpoints.values()],
          "full nominal gates",accepted,flush=True)
    return name,native,record


def handoff(out,name,record,site,fresh,filename):
    stage = out/name/"oa"
    if fresh:
        ota_ade.launch(stage,site,STATES)
    direct = {c:values["metrics"] for c,values in record["corners"].items()}
    maestro = ota_ade.review(stage,site,STATES,direct)
    launcher = "#!/bin/sh\nset -eu\ncd {}\nexec {} -restore open.il\n".format(
        shlex.quote(str(stage)),shlex.quote(str(site["virtuoso"])))
    if fresh:
        (out/filename).write_text(launcher,encoding="utf8")
        (out/filename).chmod(0o755)
    elif (out/filename).read_text(encoding="utf8")!=launcher:
        raise ValueError("The launcher does not open the selected physical design")
    return maestro


def persist(path,result,fresh):
    if fresh:
        save_json(path,result)
    elif read_json(path)!=result:
        raise ValueError("The saved decision or metrics differ from native evidence in "+str(path))


def qualify(out,ota,site,ref,fresh):
    designs = {}
    selected = None
    original,_ = parent(ota,site)
    if {s[0]:measure(ota/BASE/s[0],site,s,original,False) for s in STATES}!=ref["corners"]:
        raise ValueError("The original OTA measurements differ from the recorded baseline")
    for width,length in GEOMETRIES:
        name,native,record = physical_trial(out,width,length,site,ref,fresh)
        designs[name] = record
        if record["passed"] and selected is None:
            selected = name
    holdout = {}
    if selected:
        winner = designs[selected]
        folder = out/selected
        native,_ = audit(folder/"oa",site,dict(ref["sizes"],cm_input=winner["width_um"],cm_length_um=winner["length_um"]))
        for state in STATES:
            for icm in (ICM[0],sum(ICM)/2,ICM[1]):
                label = "{}-{:04d}".format(state[0],round(icm*1000))
                holdout[label] = exact_swing(folder/("joint-"+label),native,state,site,fresh,icm=icm)
    passed = bool(selected) and all(row["passed"] for rows in holdout.values() for row in rows)
    result = dict(baseline=BASE,candidate_order=[list(g) for g in GEOMETRIES],designs=designs,selected_before_holdout=selected,
                  joint_holdout=holdout,passed=passed,source_ota_sha256=sha256(ota/"summary.json"),
                  diagnosis_sha256=sha256(out/"diagnosis.json"))
    if selected:
        result["maestro"] = handoff(out,selected,designs[selected],site,fresh,"open-first.sh")
    persist(out/"summary.json",result,fresh)
    print("Selected before joint validation:",selected,"joint result:",passed,flush=True)
    return result


def midpoint_needed(first):
    if first["passed"] or first["selected_before_holdout"]!="w045-l300":
        return False
    failed = [(label,r) for label,rows in first["joint_holdout"].items() for r in rows if not r["passed"]]
    slow = first["designs"]["w045-l450"]["screen"]["ss"]
    return (bool(failed) and all(label.startswith("ff-") and abs(r["cm_error_mV"])>25 and
            r["headroom_V"]>=0 and r["tracking_mV"]<=1 for label,r in failed)
            and any(r["headroom_V"]<0 and r["limiting_device"] in ("XDUT.XC.MCTN","XDUT.XC.MCTP") for r in slow))


def refine(out,site,ref,fresh):
    if fresh:
        out.mkdir()
    name,native,record = physical_trial(out,.45,3.75,site,ref,fresh)
    grid = {}
    if record["passed"]:
        for state in STATES:
            for mv in range(950,1101,15):
                label = "{}-{:04d}".format(state[0],mv)
                folder = out/name
                scans = [exact_swing(folder/("joint-"+label+"-"+direction),native,state,site,fresh,icm=mv/1000,
                                     points=21,reverse=direction=="down") for direction in ("up","down")]
                conditions = []
                for a,b in zip(*scans):
                    if abs(a["command_V"]-b["command_V"])>1e-8:
                        raise ValueError("Forward and reverse joint sweeps do not contain the same stimuli")
                    difference = 1000*max(abs(a[k]-b[k]) for k in ("output_p_V","output_n_V"))
                    conditions.append(dict(command_V=a["command_V"],cm_magnitude_mV=max(abs(r["cm_error_mV"]) for r in (a,b)),
                            tracking_mV=max(r["tracking_mV"] for r in (a,b)),headroom_V=min(r["headroom_V"] for r in (a,b)),
                            direction_difference_mV=difference,passed=a["passed"] and b["passed"] and difference<=.5))
                grid[label] = conditions
    points = [r for rows in grid.values() for r in rows]
    passed = len(points)==693 and all(r["passed"] for r in points)
    report = dict(name=name,candidate=record,grid=grid,passed=passed,unique_conditions=len(points),
                  first_search_sha256=sha256(out.parent/"summary.json"))
    if record["passed"]:
        report["maestro"] = handoff(out,name,record,site,fresh,"open.sh")
    persist(out/"summary.json",report,fresh)
    print(name,"joint conditions passing:",sum(r["passed"] for r in points),"/",len(points),flush=True)
    return passed


def accepted_launcher(out,target,fresh):
    path = out/"open.sh"
    if target is None:
        if path.exists():
            raise ValueError("Found a delivery launcher despite failure of the joint specification")
        return
    script = "#!/bin/sh\nset -eu\nexec {}\n".format(shlex.quote(str(out/target)))
    if fresh:
        path.write_text(script,encoding="utf8")
        path.chmod(0o755)
    elif path.read_text(encoding="utf8")!=script or not path.stat().st_mode & 0o111:
        raise ValueError("The delivery launcher does not match the accepted native project")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("diagnose","run","verify"))
    parser.add_argument("--first-search-only",action="store_true",help="Reproduce the initial six candidates and their reserved joint test")
    for name in ("config","ota","out"):
        parser.add_argument("--"+name,required=True,type=Path)
    args = parser.parse_args()
    site = config_for_site(args.config.resolve())
    site["model_sha256"] = sha256(site["model_file"])
    site["model_file"] = str(site["model_file"])
    native,ref = parent(args.ota.resolve(),site)
    out = args.out.resolve()
    fresh = args.action!="verify"
    if fresh:
        out.mkdir(parents=True,exist_ok=False)
    diag = diagnose(out/"diagnosis",native,site,fresh)
    if fresh:
        save_json(out/"diagnosis.json",diag)
    elif read_json(out/"diagnosis.json")!=diag:
        raise ValueError("Saved diagnostic differs from the two native fixtures")
    for corner, modes in diag.items():
        print(corner, "CM error at +0.30 V (mV):", [(m,round(v[-1]["cm_error_mV"],4)) for m,v in modes.items()],flush=True)
    if args.action=="diagnose":
        return 0
    first = qualify(out,args.ota.resolve(),site,ref,fresh)
    target = "open-first.sh" if first["passed"] else None
    if not args.first_search_only and midpoint_needed(first):
        target = "midpoint/open.sh" if refine(out/"midpoint",site,ref,fresh) else None
    # A read-only first-search replay may share the output of a completed two-stage run.
    if fresh or not args.first_search_only or not (out/"midpoint").exists():
        accepted_launcher(out,target,fresh)
    return 0 if target else 1


if __name__=="__main__":
    try:
        sys.exit(main())
    except (OSError,ValueError,RuntimeError,KeyError,TypeError) as exc:
        print(str(exc),file=sys.stderr)
        sys.exit(2)
