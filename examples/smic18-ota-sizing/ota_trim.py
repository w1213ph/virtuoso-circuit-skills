"""Draw and measure a physical four-bit trim on the closed-loop two-stage OTA."""
import math

from ota_cm_groups import arguments
from ota_cmfb import GATE_AREA_UM2, ICM, checked_trace, performance_at, persist, swing_points, swing_source
from ota_matching import fullscale_copy
from ota_measure import samples
from ota_native import audit, build, hierarchy
from ota_noise import TRIM_GROUPS
from ota_range import replace_fixture_field
from ota_robust import dynamic, load_design
from ota_trim_views import CODE_PINS, bit_gain
from bias_sim import STATES
from spectre_run import read_json, sha256


GEOMETRY = {"input_pair":(1.65,1.9),"cm_loads":(5.15,1.5)}
BANK = dict(ohms=[750,1250,2220,4150],switch_um=1.5,inverter_n_um=.45,
            inverter_p_um=.65,length_um=.18,code=8)
BANKS = {"balanced":BANK,"wide":dict(BANK,ohms=[1200,1900,3200,6000]),
         "centered":dict(BANK,ohms=[750,1250,2220,3650])}


def applied(native,code):
    for bit in range(4):
        native = replace_fixture_field(native,"EB"+str(bit),"gain",bit_gain(BANK["code"],bit),bit_gain(code,bit))
    return native


def physical_bank(folder,site,sizes,reference,fresh,bank,code=None):
    controls = bank if code is None else dict(bank,code=code)
    candidate = dict(sizes,paired_geometry=GEOMETRY,source_trim=controls)
    if fresh:
        build(folder,site,candidate)
    native,physical = audit(folder,site,candidate)
    gate_added = bank["length_um"]*(8*bank["switch_um"]+4*(bank["inverter_n_um"]+bank["inverter_p_um"]))
    delta = 2*sum(w*l-sizes[role]*sizes["length_um"]
                  for (w,l),role in ((GEOMETRY["input_pair"],"input"),(GEOMETRY["cm_loads"],"load")))
    base = reference["area"]
    measured = physical["area"]
    extra = {"RS"+side+str(bit) for side in ("P","N") for bit in range(4)}
    strip = round(math.fsum(physical["passives"][r]["segment_length_um"]*physical["passives"][r]["segment_width_um"]
                          *physical["passives"][r]["segments"] for r in extra),6)
    if (not math.isclose(measured["mos_gate_um2"],base["mos_gate_um2"]+gate_added+delta,abs_tol=1e-6)
            or not math.isclose(measured["resistor_strip_um2"],base["resistor_strip_um2"]+strip,abs_tol=1e-6)
            or measured["mim_plate_um2"]!=base["mim_plate_um2"]
            or {k:v for k,v in physical["passives"].items() if k not in extra}!=reference["passives"]):
        raise ValueError("The native area change differs from the eight resistors and named MOS changes")
    return native,dict(physical=physical,trim_gate_um2=gate_added,trim_strip_um2=strip,
                       mos_limit_um2=GATE_AREA_UM2,mos_within_budget=measured["mos_gate_um2"]<=GATE_AREA_UM2)


def measured_code(folder,net,state,icm,code,site,fresh):
    source = swing_source(applied(net,code),state,site,icm,3,False,False)
    stems = ["XDUT.XI.SP0","XDUT.XI.SN0","XDUT.XI.TAIL"]
    controls = [name for name,_ in CODE_PINS]+["XDUT.XI.TB"+str(i) for i in range(4)]
    marker = "cm_sweep dc param=CMCOMMAND"
    if source.count(marker)!=1:
        raise ValueError("Cannot locate the three-point closed-loop differential sweep")
    source = source.replace(marker,"save "+" ".join(controls+stems)+"\n"+marker)
    raw = checked_trace(folder,source,"cm_sweep.dc",site,fresh)
    outputs = swing_points(raw,state,icm)
    saved = samples(raw,"CMCOMMAND",tuple(controls+stems))
    worst = 0
    for i in range(4):
        direct = int(bit_gain(code,i))
        for node,level in ((controls[i],direct),(controls[4+i],1-direct)):
            worst = max(worst,max(abs(v-state[2]*level) for v in saved[node]))
    if worst>1e-3:
        raise ValueError("Trim word or its on-chip complement failed to reach the requested rails")
    points = []
    for j,row in enumerate(outputs):
        points.append(dict(command_V=row["command_V"],signed_error_mV=1000*(row["output_p_V"]-row["output_n_V"]-row["command_V"]),
                      cm_error_mV=row["cm_error_mV"],headroom_mV=1000*row["headroom_V"],power_uW=row["power_uW"],
                      source_diff_mV=1000*(saved[stems[0]][j]-saved[stems[1]][j]),passed=row["passed"]))
    return dict(points=points,raw_sha256=sha256(raw),max_control_error_mV=1000*worst)


def code_summary(codes):
    if set(codes)!={str(i) for i in range(16)}:
        raise ValueError("Require the complete four-bit code sweep")
    errors = [codes[str(i)]["points"][1]["signed_error_mV"] for i in range(16)]
    steps = [b-a for a,b in zip(errors,errors[1:])]
    ordered = sorted(errors)
    return dict(code0_mV=errors[0],code15_mV=errors[-1],min_mV=min(errors),max_mV=max(errors),
                max_gap_mV=max(b-a for a,b in zip(ordered,ordered[1:])),
                monotonic=all(d>0 for d in steps) or all(d<0 for d in steps),
                nearest_zero_code=min(range(16),key=lambda i:(abs(errors[i]),i)))


def characterize(args,site,fresh):
    _,sizes,reference = load_design(args.ota,args.cmfb,site)
    # A footprint comparison is against a physically audited upstream candidate.
    old = read_json(args.matching/"sensitivity.json")["probes"]["load-high"]
    _,area = audit(args.matching/"load-high/oa",site,dict(sizes,paired_geometry=old["geometry"]))
    if area!=old["physical"]:
        raise ValueError("Earlier device sizing evidence differs from its native OA export")
    if fresh:
        args.out.mkdir(parents=True)
    bank = BANKS[args.bank]
    native,budget = physical_bank(args.out/"oa",site,sizes,reference,fresh,bank)
    curves,summary = {},{}
    for state in STATES:
        for icm in ICM:
            label = "{}-{}".format(state[0],round(icm*1000))
            curve = {str(code):measured_code(args.out/(label+"-code"+str(code)),native,state,icm,code,site,fresh)
                     for code in range(16)}
            curves[label] = curve
            summary[label] = code_summary(curve)
            print(label,summary[label],flush=True)
    code = summary["tt-950"]["nearest_zero_code"]
    fixed = applied(native,code)
    if code!=bank["code"]:
        released,check = physical_bank(args.out/"fixed-code-oa",site,sizes,reference,fresh,bank,code)
        if hierarchy(fixed)!=hierarchy(released) or budget!=check:
            raise ValueError("The editable fixed-code bench differs from the chosen physical code")
        fixed = released
    fixed_rows = [p for curve in curves.values() for p in curve[str(code)]["points"]]
    performance = {s[0]:performance_at(args.out,"source-trim",fixed,site,s,budget["physical"],fresh,TRIM_GROUPS)
                   for s in STATES}
    large = dynamic(args.out/"fullscale",fullscale_copy(fixed),site,fresh)
    result = dict(baseline_sha256=sha256(args.matching/"sensitivity.json"),geometry=GEOMETRY,bank=bank,
        budget=budget,curves=curves,curve_summary=summary,fixed_code=code,
        fixed_static_passed=sum(row["passed"] for row in fixed_rows),zero_input_open_loop=performance,fullscale=large)
    # Persist JSON-native tuples so verification replays the exact same decision.
    result["geometry"] = {k:list(v) for k,v in GEOMETRY.items()}
    persist(args.out/"summary.json",result,fresh)
    return result


if __name__=="__main__":
    options,settings = arguments(__doc__,banks=tuple(BANKS))
    report = characterize(options,settings,options.action=="run")
    print("TT-calibrated nominal code",report["fixed_code"],"joint DC endpoints",report["fixed_static_passed"],"/18")
    print("Open-loop zero-input gain and noise are diagnostic. Use ota_trim_ac.py at the closed-loop bias for acceptance.")
