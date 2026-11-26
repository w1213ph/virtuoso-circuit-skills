"""Measure a native OTA's static input common-mode and differential output ranges on a fixed grid."""
import argparse
import csv
import io
import json
import math
from pathlib import Path
import re
import sys

from ota import candidates, config_for_site
from ota_native import audit
from ota_measure import LIMITS, NODES, OP_DEVICES, OP_SAVES, measure, samples
from bias_sim import STATES, analysis, model_header
from spectre_run import read_json, save_json, sha256

TRIALS = {
    "cm_forward": ("cm",False,0), "cm_reverse": ("cm",True,0),
    "plus": ("cm",False,1e-7), "minus": ("cm",False,-1e-7),
    "plus_half": ("cm",False,5e-8), "minus_half": ("cm",False,-5e-8),
    "swing_forward": ("swing",False,0), "swing_reverse": ("swing",True,0),
}
STEP_COUNT = {"cm":80,"swing":160}


def replace_fixture_field(native, instance, key, old, new):
    pattern = r"(?m)^(?P<head>"+re.escape(instance)+r"\s+\([^\n)]+\)\s+\w+[^\n]*?\b"+key+r"=)"+re.escape(old)+r"(?=\s|$)"
    updated, count = re.subn(pattern,lambda m:m["head"]+new,native)
    if count != 1:
        raise ValueError("Expected one unchanged native fixture setting for "+instance+"/"+key)
    return updated


def sweep_deck(native, site, state, mode, reverse, delta):
    fraction = "(1-RANGEIDX)" if reverse else "RANGEIDX"
    if mode=="cm":
        native = replace_fixture_field(native,"EVIC","gain","0.6",fraction)
        command = "{:.12g}".format(delta)
    else:
        command = "((2*"+fraction+"-1)*VDDVAL)"
    native = replace_fixture_field(native,"VD","dc","0",command)
    header = model_header(site,state)
    header[3:3] = ['include "{}" section={}_{}'.format(site["model_file"],kind,state[0]) for kind in ("res","mim")]
    saves = NODES + (() if delta else OP_SAVES)
    return "\n".join(header+["parameters VDDVAL={:g} BETA={} RANGEIDX=0".format(state[2],int(mode=="swing")),
                            native,"saveOptions options save=selected","save "+" ".join(saves),
                            "range_dc dc param=RANGEIDX start=0 stop=1 step={:.12g}".format(1/STEP_COUNT[mode])])+"\n"


def read_curve(path, state, mode, reverse, delta):
    signals = NODES + (() if delta else OP_SAVES)
    raw = samples(path,"RANGEIDX",signals)
    count, supply = STEP_COUNT[mode],state[2]
    if len(raw["RANGEIDX"])!=count+1 or any(abs(x-i/count)>1e-8 for i,x in enumerate(raw["RANGEIDX"])):
        raise ValueError("Missing points on native DC sweep")
    curve = []
    for i,u in enumerate(raw["RANGEIDX"]):
        fraction = 1-u if reverse else u
        icm = supply*(fraction if mode=="cm" else .6)
        command = delta if mode=="cm" else supply*(2*fraction-1)
        read = lambda name:raw[name][i]
        vin = read("INP")-read("INN")
        in_common = (read("INP")+read("INN"))/2
        if (abs(read("VDD")-supply)>1e-6 or abs(read("VCM")-.5*supply)>1e-6 or abs(in_common-icm)>1e-6
                or abs(read("CMD")-command)>1e-10 or (mode=="cm" and abs(vin-delta)>1e-10)):
            raise ValueError("Native supply, reference or applied input differs from the requested sweep")
        op,on = read("OUTP"),read("OUTN")
        row = dict(input_cm_V=in_common,differential_command_V=read("CMD"),output_diff_V=op-on,output_cm_V=(op+on)/2,
                   input_diff_V=vin,output_p_V=op,output_n_V=on,power_uW=-read("VSUP:p")*supply*1e6)
        if not delta:
            margins = {dev:abs(read(dev+":vds"))-abs(read(dev+":vdsat")) for dev in OP_DEVICES}
            limiting = min(margins,key=margins.get)
            row.update(limiting_device=limiting,headroom_V=margins[limiting])
        curve.append(row)
    return curve[::-1] if reverse else curve


def contiguous(rows, anchor, key):
    if rows[anchor]["failed"]:
        return dict(bounds_V=None,anchor_failure=rows[anchor])
    lo = hi = anchor
    while lo>0 and not rows[lo-1]["failed"]:
        lo-=1
    while hi+1<len(rows) and not rows[hi+1]["failed"]:
        hi+=1
    return dict(bounds_V=[rows[lo][key],rows[hi][key]],passed_endpoints=[rows[lo],rows[hi]],
                adjacent_failures=[rows[lo-1] if lo else None,rows[hi+1] if hi+1<len(rows) else None],
                passing_grid_points=hi-lo+1,other_passing_points=sum(not r["failed"] for r in rows)-(hi-lo+1))


def limits_at(row, reverse, supply):
    row["sweep_difference_mV"] = 1000*max(abs(row[k]-reverse[k]) for k in ("output_p_V","output_n_V"))
    return {
        "output_common_mode":abs(row["output_cm_V"]-supply/2)<=LIMITS["cm_error_mV"]*1e-3,
        "saturation":row["headroom_V"]>=0,
        "output_within_rails":0<=min(row["output_p_V"],row["output_n_V"])<=max(row["output_p_V"],row["output_n_V"])<=supply,
        "supply_current":row["power_uW"]>0,
        "sweep_direction":row["sweep_difference_mV"]<=0.5,
    }


def summarize(curves,supply,baseline):
    for i,row in enumerate(curves["cm_forward"]):
        checks = limits_at(row,curves["cm_reverse"][i],supply)
        gains = []
        for positive,negative in (("plus","minus"),("plus_half","minus_half")):
            high,low = curves[positive][i],curves[negative][i]
            gains.append((high["output_diff_V"]-low["output_diff_V"])/(high["input_diff_V"]-low["input_diff_V"]))
        gain = gains[1]
        row["gain_dB"] = 20*math.log10(gain) if gain>0 else None
        checks.update(gain=row["gain_dB"] is not None and row["gain_dB"]>LIMITS["gain_dB"],
                      derivative_convergence=math.isclose(gains[0],gain,rel_tol=.01,abs_tol=.01))
        row["failed"] = [k for k,v in checks.items() if not v]
    cm_anchor = round(.6*STEP_COUNT["cm"])
    nominal = curves["cm_forward"][cm_anchor]
    if nominal["gain_dB"] is None or abs(nominal["gain_dB"]-baseline["gain_dB"])>.35:
        raise ValueError("Nominal finite-difference gain disagrees with native 1 Hz differential AC gain")
    for i,row in enumerate(curves["swing_forward"]):
        checks = limits_at(row,curves["swing_reverse"][i],supply)
        row["tracking_error_mV"] = 1000*abs(row["output_diff_V"]-row["differential_command_V"])
        checks["tracking"] = row["tracking_error_mV"]<=1
        row["failed"] = [k for k,v in checks.items() if not v]
    cm = contiguous(curves["cm_forward"],cm_anchor,"input_cm_V")
    swing = contiguous(curves["swing_forward"],STEP_COUNT["swing"]//2,"differential_command_V")
    return dict(input_cm=cm,output_diff=swing,nominal_dc_gain_dB=nominal["gain_dB"],nominal_ac_gain_dB=baseline["gain_dB"],
                grid_step_V=supply/STEP_COUNT["cm"])


def covers(bounds, requested):
    return bounds is not None and bounds[0]<=requested[0]+1e-9 and bounds[1]>=requested[1]-1e-9


def export_points(path, curves, fresh):
    columns = ("input_cm_V","differential_command_V","output_diff_V","output_cm_V","output_p_V","output_n_V",
               "gain_dB","tracking_error_mV","headroom_V","limiting_device","sweep_difference_mV","power_uW")
    stream = io.StringIO(newline="")
    writer = csv.writer(stream,lineterminator="\n")
    writer.writerow(("sweep",*columns,"failed"))
    for mode in ("cm","swing"):
        for row in curves[mode+"_forward"]:
            writer.writerow((mode,*(row.get(k) for k in columns)," ".join(row["failed"])))
    raw = stream.getvalue().encode("ascii")
    if fresh:
        path.write_bytes(raw)
    elif path.read_bytes()!=raw:
        raise ValueError("Exported DC points differ from raw native evidence: "+str(path))


def evaluate(site, ota, out, name, icm, swing, fresh):
    prior = read_json(ota/"summary.json")
    if (prior["model_sha256"]!=site["model_sha256"] or prior["pdk_revision"]!=site["pdk_revision"]
            or prior["pdk_library"]!=site["pdk_library"] or name not in prior["candidates"]
            or name not in {n for n,_ in candidates()}):
        raise ValueError("The requested candidate and original PDK must match the saved OTA search")
    ref = prior["candidates"][name]
    native, physical = audit(ota/name/"oa",site,ref["sizes"])
    if physical!=ref["physical"]:
        raise ValueError("Device sizes or native hierarchy differ from recorded OTA")
    if fresh:
        out.mkdir(parents=True,exist_ok=False)
    corners = {}
    for state in STATES:
        corner = state[0]
        baseline = measure(ota/name/corner,site,state,native,False)
        if baseline!=ref["corners"][corner]:
            raise ValueError("Baseline differs from the original native circuit at "+corner)
        curves = {}
        for label,(mode,reverse,delta) in TRIALS.items():
            folder = out/(corner+"-"+label)
            raw = analysis(folder,sweep_deck(native,site,state,mode,reverse,delta),"range_dc.dc",site,site["spectre"] if fresh else None)
            job = read_json(folder/"job.json")
            if job["warnings"] or any(sha256(Path(k))!=v for k,v in job["dependencies"].items()):
                raise ValueError("Review the DC sweep warnings or a changed model")
            curves[label] = read_curve(raw,state,mode,reverse,delta)
        result = summarize(curves,state[2],baseline)
        table = out/(corner+"-points.csv")
        export_points(table,curves,fresh)
        result.update(input_request_pass=covers(result["input_cm"]["bounds_V"],icm),
                      swing_request_pass=covers(result["output_diff"]["bounds_V"],swing),original_checks_pass=baseline["passed"],
                      points_file=table.name,points_sha256=sha256(table))
        corners[corner] = result
        print(corner,result["input_cm"]["bounds_V"],result["output_diff"]["bounds_V"],flush=True)
    passed = all(c["original_checks_pass"] and c["input_request_pass"] and c["swing_request_pass"] for c in corners.values())
    return dict(candidate=name,input_request_V=icm,swing_request_V=swing,corners=corners,passed=passed,
                native_dc_sweeps=len(TRIALS)*len(STATES),original_analyses_replayed=5*len(STATES),
                source_ota_sha256=sha256(ota/"summary.json"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("run","verify","evaluate"))
    for field in ("config","ota","out"):
        parser.add_argument("--"+field,required=True,type=Path)
    parser.add_argument("--candidate")
    parser.add_argument("--icm-v",nargs=2,type=float)
    parser.add_argument("--vdiff-v",nargs=2,type=float)
    args = parser.parse_args()
    site = config_for_site(args.config.expanduser().resolve())
    site["model_file"] = str(site["model_file"])
    site["model_sha256"] = sha256(Path(site["model_file"]))
    out,ota = args.out.expanduser().resolve(),args.ota.expanduser().resolve()
    fresh = args.action=="run"
    original = read_json(ota/"summary.json")
    saved = {} if fresh else read_json(out/"summary.json")
    candidate = args.candidate or saved.get("candidate") or original["selected"]
    icm = args.icm_v or saved.get("input_request_V",[.95,1.10])
    swing = args.vdiff_v or saved.get("swing_request_V",[-.30,.30])
    if not all(math.isfinite(v) for v in icm+swing) or not (0<=icm[0]<icm[1] and swing[0]<swing[1]):
        raise ValueError("Supply two increasing finite input and output range bounds in volts")
    if not fresh and (candidate!=saved["candidate"] or (args.action=="verify" and (icm!=saved["input_request_V"] or swing!=saved["swing_request_V"]))):
        raise ValueError("Verify the original experiment. Use evaluate for a different range on the same candidate.")
    report = evaluate(site,ota,out,candidate,icm,swing,fresh)
    if fresh:
        save_json(out/"summary.json",report)
    elif args.action=="verify" and saved!=report:
        raise ValueError("Saved result differs from the native sweeps")
    print("Static range requirements","PASS" if report["passed"] else "FAIL")
    return 0 if report["passed"] else 1


if __name__=="__main__":
    try:
        sys.exit(main())
    except (OSError,ValueError,RuntimeError,KeyError,TypeError,OverflowError) as exc:
        print(str(exc),file=sys.stderr)
        sys.exit(2)
