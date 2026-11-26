"""Paired, three-corner, local MOS mismatch on the SI-exported statistical OTA."""
import hashlib
import math
from pathlib import Path
import re
import struct

from ota_cmfb import ICM, checked_trace, swing_points, swing_source
from ota_measure import OP_DEVICES, samples
from bias_sim import STATES
from spectre_run import read_json, run_spectre, save_json, sha256


SEEDS = (64211,97337)
COUNT = 32
ARMS = ("all","off","input_pair","cmfb")
LIMITS = ("tracking","common","saturation","rails","supply")
CM_GROUPS = {"cm_detector": ("MDETP","MDETN","MCREFP","MCREFN"),
             "cm_load": ("MCM","MDMY"), "cm_tail": ("MCTP","MCTN")}


def mask_mismatch(native,arm,extra_devices=()):
    """Disable only the marked devices' local mismatch in a diagnostic copy of the original SI fixture."""
    if arm not in ARMS and arm not in CM_GROUPS and arm!="outside_cm":
        raise ValueError("Unknown mismatch experiment")
    devices = {name.split(".")[-1]:name for name in tuple(OP_DEVICES)+tuple(extra_devices)}
    if len(devices)!=len(OP_DEVICES)+len(extra_devices):
        raise ValueError("Statistical DUT instances must have distinct leaf names")
    seen = set()
    def edit(match):
        name,line = match[1],match[0]
        if name not in devices or name in seen:
            raise ValueError("Unexpected or repeated physical MOS in statistical fixture")
        seen.add(name)
        on = (arm=="all" or (arm=="input_pair" and name in ("MINP","MINN"))
              or (arm=="cmfb" and ".XC." in devices[name]) or name in CM_GROUPS.get(arm,())
              or (arm=="outside_cm" and ".XC." not in devices[name]))
        changed,n = re.subn(r"\bmismod=(?:1|\(1\))(?=\s|$)","mismod=0",line)
        if n!=1:
            raise ValueError("Missing enabled PDK mismatch selector on "+name)
        return line if on else changed
    out = re.sub(r"(?m)^[ \t]*(M\w+)[ \t]+\([^\n)]+\)[ \t]+[np]18_ckt\b[^\n]*",edit,native)
    if seen!=set(devices):
        raise ValueError("Require all audited statistical MOS masters")
    return out


def sampled_ids(descriptor,data,count,extra_devices=()):
    """Bind each numeric draw to named local scopes; verify local parameters actually change on all DUT MOS."""
    lines = [line.split() for line in descriptor.read_text(encoding="utf8").splitlines() if line.strip()]
    if len(lines)<2 or any(len(l)!=1 for l in lines[1:]):
        raise ValueError("Malformed native local-mismatch parameter declaration")
    fields = lines[0]
    scopes = [line[0] for line in lines[1:]]
    required = set(OP_DEVICES).union(extra_devices)
    if len(set(fields))!=len(fields) or len(set(scopes))!=len(scopes) or not required<=set(scopes):
        raise ValueError("Missing or duplicate local mismatch fields or MOS instance scopes")
    vectors = re.split(r"(?m)^#(\d+)\s*$",data.read_text(encoding="utf8"))
    if vectors[0].strip() or len(vectors)!=1+2*count:
        raise ValueError("Incomplete native mismatch draws")
    ids = {}
    varied = {name:set() for name in required}
    for draw,raw in zip(vectors[1::2],vectors[2::2]):
        # Spectre writes one numeric row per named scope, in descriptor order.
        rows = [tuple(map(float,line.split())) for line in raw.splitlines() if line.strip()]
        n = int(draw)
        if n in ids or len(rows)!=len(scopes):
            raise ValueError("Invalid number of mismatch scopes or duplicate draw")
        digest = hashlib.sha256()
        for name,values in zip(scopes,rows):
            if len(values)!=len(fields) or not all(map(math.isfinite,values)):
                raise ValueError("Invalid local mismatch fields on "+name)
            digest.update(struct.pack(">{}d".format(len(fields)),*values))
            if name in varied:
                varied[name].add(values)
        ids[n] = digest.hexdigest()
    if (set(ids)!=set(range(1,count+1)) or len(set(ids.values()))!=count or
            any(len(values)!=count for values in varied.values())):
        raise ValueError("Local draws must vary for each physical MOS and have complete distinct IDs")
    return [ids[i] for i in range(1,count+1)],len(fields)


def classification(row,vdd):
    return dict(tracking=row["tracking_mV"]<=1,common=abs(row["cm_error_mV"])<=25,saturation=row["headroom_V"]>=0,
                rails=0<=min(row["output_p_V"],row["output_n_V"])<=max(row["output_p_V"],row["output_n_V"])<=vdd,
                supply=row["power_uW"]>0)


def record(folder,native,state,icm,site,arm,seed,fresh,extra_devices=(),control_levels=None):
    source = swing_source(mask_mismatch(native,arm,extra_devices),state,site,icm,3,False,False)
    head,analysis = source.rstrip().rsplit("\n",1)
    if not analysis.startswith("cm_sweep dc "):
        raise ValueError("Cannot identify the verified differential sweep")
    if control_levels:
        head += "\nsave "+" ".join(sorted(control_levels))
    command = (head+"\nmc montecarlo variations=mismatch donominal=no savefamilyplots=yes savemismatchparams=yes"
               +" numruns={} seed={} {{\n".format(COUNT,seed)+analysis+"\n}\n")
    raw = folder/"psf"
    if fresh:
        folder.mkdir()
        (folder/"input.scs").write_text(command,encoding="utf8")
        _,dependencies,warnings = run_spectre(site["spectre"],folder,Path(site["model_file"]),["mc-001_cm_sweep.dc"])
        if warnings:
            raise ValueError("Review native warnings before accepting statistical evidence")
        files = [folder/"input.scs",folder/"spectre.out",raw/"mc.mismatch.mcparam",raw/"mc.mismatch.mcdata"]
        files.extend(sorted(raw.glob("mc-*_cm_sweep.dc")))
        save_json(folder/"job.json",dict(files={str(p.relative_to(folder)):sha256(p) for p in files},dependencies=dependencies))
    job = read_json(folder/"job.json")
    expected = ["psf/mc-{:03d}_cm_sweep.dc".format(i) for i in range(1,COUNT+1)]
    names = set(expected+["input.scs","spectre.out","psf/mc.mismatch.mcparam","psf/mc.mismatch.mcdata"])
    log = (folder/"spectre.out").read_text(errors="replace")
    loaded = {str(Path(v.strip()).resolve()) for v in re.findall(r"(?m)^Reading file:\s*([^\n]+)$",log)}
    dependencies = job["dependencies"]
    if (set(job["files"])!=names or {p.name for p in raw.glob("mc-*_cm_sweep.dc")}!={Path(p).name for p in expected}
            or any(sha256(folder/k)!=v for k,v in job["files"].items())
            or any(sha256(Path(k))!=v for k,v in dependencies.items()) or not set(dependencies)<=loaded
            or dependencies.get(site["model_file"])!=site["model_sha256"]
            or not re.search(r"spectre completes with 0 errors?, 0 warnings?",log,re.I)
            or (folder/"input.scs").read_text(encoding="utf8")!=command):
        raise ValueError("The mismatch deck, dependencies, log or child PSF changed")
    ids,field_count = sampled_ids(raw/"mc.mismatch.mcparam",raw/"mc.mismatch.mcdata",COUNT,extra_devices)
    observed = {k:[] for k in LIMITS}
    rows = []
    for i,file in enumerate(expected):
        points = swing_points(folder/file,state,icm)
        if control_levels:
            measured = samples(folder/file,"CMCOMMAND",tuple(control_levels))
            if any(any(abs(x-level*state[2])>1e-3 for x in measured[node]) for node,level in control_levels.items()):
                raise ValueError("A physical trim bit or its complement missed the commanded rail")
        gates = [classification(r,state[2]) for r in points]
        for limit in LIMITS:
            observed[limit].append(all(g[limit] for g in gates))
        rows.append([dict(command_V=r["command_V"],signed_diff_error_mV=1000*(r["output_p_V"]-r["output_n_V"]-r["command_V"]),
                          cm_error_mV=r["cm_error_mV"],headroom_mV=1000*r["headroom_V"],
                          limiting_device=r["limiting_device"],failed=[k for k in LIMITS if not g[k]]) for r,g in zip(points,gates)])
        if control_levels:
            for point,measurement in zip(rows[-1],points):
                point["power_uW"] = measurement["power_uW"]
    return dict(ids=ids,descriptor_sha256=job["files"]["psf/mc.mismatch.mcparam"],fields_per_scope=field_count,
                passed_per_gate={k:sum(v) for k,v in observed.items()},samples=rows,
                job_sha256=sha256(folder/"job.json"))


def nominal_control(out,site,state,icm,original,statistical,fresh):
    observed = []
    for role,net in (("ordinary",original),("statistical",statistical)):
        raw = checked_trace(out/(role+"-"+state[0]+"-"+str(round(icm*1000))),
                            swing_source(net,state,site,icm,3,False,False),"cm_sweep.dc",site,fresh)
        fields = tuple(d+":"+p for d in OP_DEVICES for p in ("vds","vdsat"))
        points = samples(raw,"CMCOMMAND",fields)
        margins = [abs(a)-abs(b) for d in OP_DEVICES for a,b in zip(points[d+":vds"],points[d+":vdsat"])]
        observed.append((swing_points(raw,state,icm),margins))
    (a,ha),(b,hb) = observed
    metrics = dict(max_diff_mV=max(abs((p["output_p_V"]-p["output_n_V"])-(q["output_p_V"]-q["output_n_V"]))*1000 for p,q in zip(a,b)),
        max_common_mV=max(abs(p["cm_error_mV"]-q["cm_error_mV"]) for p,q in zip(a,b)),
        max_headroom_mV=max(abs(x-y)*1000 for x,y in zip(ha,hb)),
        max_power_relative=max(abs(p["power_uW"]/q["power_uW"]-1) for p,q in zip(a,b)))
    if metrics["max_diff_mV"]>.3 or metrics["max_common_mV"]>.3 or metrics["max_headroom_mV"]>3 or metrics["max_power_relative"]>.02:
        raise ValueError("Ordinary and statistical unvaried devices are not equivalent in "+state[0]+": "+str(metrics))
    return metrics


def coverage(cases):
    if not cases:
        raise ValueError("Missing sample comparison")
    first = next(iter(cases.values()))
    if any(r["ids"]!=first["ids"] or r["descriptor_sha256"]!=first["descriptor_sha256"]
           or len(r["samples"])!=len(r["ids"]) for r in cases.values()):
        raise ValueError("Cannot combine electrically different random draws or local parameter definitions")
    joint = []
    for i in range(len(first["ids"])):
        failed = {k for r in cases.values() for p in r["samples"][i] for k in p["failed"]}
        joint.append(failed)
    return dict(total=len(joint),passed=sum(not f for f in joint),
                passed_per_gate={k:sum(k not in f for f in joint) for k in LIMITS})


def response_spread(case):
    largest = 0.0
    for at_command in zip(*case["samples"]):
        for metric in ("signed_diff_error_mV","cm_error_mV"):
            values = [row[metric] for row in at_command]
            largest = max(largest,max(values)-min(values))
    return largest


def experiment(root,site,ordinary,statistical,fresh):
    out = root/"mismatch"
    if fresh:
        out.mkdir()
    controls,groups,paired,diagnostic = {},{},{},{}
    for state in STATES:
        for icm in ICM:
            label = "{}-{}".format(state[0],round(1000*icm))
            controls[label] = nominal_control(out,site,state,icm,ordinary,statistical,fresh)
            for seed in SEEDS:
                key = str(seed)
                row = record(out/(label+"-"+key+"-all"),statistical,state,icm,site,"all",seed,fresh)
                groups.setdefault(key,{})[label] = row
            if icm!=ICM[0]:
                continue
            arms = {"all":groups[str(SEEDS[0])][label]}
            for arm in ARMS[1:]:
                arms[arm] = record(out/(label+"-"+str(SEEDS[0])+"-"+arm),statistical,state,icm,site,arm,SEEDS[0],fresh)
            coverage(arms)
            active,disabled = [response_spread(arms[k]) for k in ("all","off")]
            if active<=.001 or disabled>.005 or active<=100*disabled:
                raise ValueError("Could not separate active local MOS mismatch from the disabled control")
            diagnostic[state[0]] = dict(arms={k:coverage({k:r}) for k,r in arms.items()},
                    active_response_spread_mV=active,disabled_response_spread_mV=disabled)
    for seed,cases in groups.items():
        paired[seed] = coverage(cases)
    a,b = [next(iter(groups[str(seed)].values()))["ids"] for seed in SEEDS]
    if set(a)&set(b):
        raise ValueError("The two predeclared seed sets share random draws")
    passed = all(r["passed"]==COUNT for r in paired.values())
    return dict(nominal_controls=controls,seeds=list(SEEDS),runs_per_seed=COUNT,statistics="local MOS mismatch",
                cases=groups,paired_batches=paired,diagnostic=diagnostic,mismatch_status="passed" if passed else "sampled_failures")
