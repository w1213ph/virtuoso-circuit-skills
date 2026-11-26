"""Measure gain and noise at the operating point of the loaded, trimmed, closed-loop OTA."""
import math

from ota_cm_groups import arguments
from ota_cmfb import BAND, NOISE_UV, checked_trace, joint_grid, persist
from ota_measure import MODES, NODES, deck, samples
from ota_native import hierarchy
from ota_noise import NOISE, TRIM_GROUPS, assess, export_csv, noise_test, spectrum
from ota_range import replace_fixture_field
from ota_robust import load_design
from ota_trim import BANKS, applied, physical_bank
from bias_sim import STATES
from spectre_run import read_json, sha256


def local_transfer(folder,net,state,site,icm,reference,fresh):
    delta = 5e-8
    sweep = replace_fixture_field(net,"VD","dc","0","({:.16g}+DBIAS)".format(reference))
    source = deck(site,state,sweep,"dc")
    if source.count(" BETA=1\n")!=1 or source.count("\ndc dc\n")!=1:
        raise ValueError("Cannot isolate the differential loop for the bias check")
    source = source.replace(" BETA=1\n"," BETA=0 DBIAS=0\n")
    source = source.replace("\ndc dc\n","\nbias_dc dc param=DBIAS start=-100n stop=100n step=50n\n")
    transfer = checked_trace(folder,source,"bias_dc.dc",site,fresh)
    points = samples(transfer,"DBIAS",NODES)
    if len(points["DBIAS"])!=5:
        raise ValueError("Expected five bias perturbations")
    for i,axis in enumerate(points["DBIAS"]):
        if max(abs(axis-delta*(i-2)),abs(points["INP"][i]-points["INN"][i]-reference-axis),
               abs((points["INP"][i]+points["INN"][i])/2-icm),abs(points["VCM"][i]-state[2]/2))>1e-9:
            raise ValueError("Applied DC perturbation or common-mode point changed")
    diff = [p-n for p,n in zip(points["OUTP"],points["OUTN"])]
    slopes = [(diff[2+j]-diff[2-j])/(2*j*delta) for j in (1,2)]
    if min(slopes)<=0 or not math.isclose(*slopes,rel_tol=.01):
        raise ValueError("Finite-difference gain failed the perturbation-size check")
    return transfer,points,slopes[0]


def station(folder,native,state,site,icm,noise,fresh):
    if fresh:
        folder.mkdir()
    net = replace_fixture_field(native,"EVIC","gain","0.6","({:.16g}/VDDVAL)".format(icm))
    closed = checked_trace(folder/"closed",deck(site,state,net,"dc"),"dc.dc",site,fresh)
    dc = {k:v[0] for k,v in samples(closed,None,NODES).items()}
    closed_input = dc["INP"]-dc["INN"]
    if max(abs(dc["CMD"]),abs(dc["OUTP"]-dc["OUTN"]+closed_input),abs((dc["INP"]+dc["INN"])/2-icm),
           abs(dc["VDD"]-state[2]),abs(dc["VCM"]-state[2]/2))>1e-7:
        raise ValueError("The reference is not the requested closed-loop DC operating point")
    reference = closed_input
    for attempt in range(3):
        target = folder/("bias" if attempt==0 else "bias-"+str(attempt))
        transfer,points,slope = local_transfer(target,net,state,site,icm,reference,fresh)
        shift = max(abs(points[k][2]-dc[k]) for k in ("OUTP","OUTN","XDUT.BN"))
        if shift<=.0005:
            break
        reference += (dc["OUTP"]-dc["OUTN"]-points["OUTP"][2]+points["OUTN"][2])/slope
        if abs(reference-closed_input)>1e-6:
            raise ValueError("Numerical bias correction exceeds one microvolt")
    else:
        raise ValueError("Opening the loop moved the output/bias operating point by {:.4g} V".format(shift))
    bias = replace_fixture_field(net,"VD","dc","0","{:.16g}".format(reference))
    ac_source = deck(site,state,bias,"gain")
    ac = checked_trace(folder/"ac",ac_source,"ac.ac",site,fresh)
    low = samples(ac,"freq",("CMD","INP","INN","OUTP","OUTN"))
    if low["freq"][0]!=1 or any(abs(p-n-1)>1e-6 or abs(c-1)>1e-6 for c,p,n in zip(low["CMD"],low["INP"],low["INN"])):
        raise ValueError("Differential AC stimulus or low frequency changed")
    gain = 20*math.log10(abs(low["OUTP"][0]-low["OUTN"][0]))
    finite = 20*math.log10(slope)
    if abs(gain-finite)>.35:
        raise ValueError("Bias-matched 1 Hz AC gain disagrees with its DC derivative")
    result = dict(input_cm_V=icm,dc_input_diff_mV=1000*reference,max_operating_point_move_mV=1000*shift,
        gain_dB=gain,finite_difference_dB=finite,raw_sha256={n:sha256(f) for n,f in (("closed",closed),("bias",transfer),("ac",ac))})
    if attempt:
        result.update(bias_refinements=attempt,closed_input_diff_mV=1000*closed_input)
    if noise:
        analysis = MODES["gain"][0]
        if ac_source.count(analysis)!=1:
            raise ValueError("Cannot identify the bias-matched AC analysis")
        raw = checked_trace(folder/"noise",ac_source.replace(analysis,NOISE),"ns.noise",site,fresh)
        path = folder/"noise.csv"
        export_csv(path,spectrum(raw,ac,TRIM_GROUPS),fresh,TRIM_GROUPS)
        test = noise_test("fixed-physical-code",state,BAND,NOISE_UV,path.name)
        rms,fractions = assess(path,test,TRIM_GROUPS)
        result.update(noise_uVrms=rms,noise_power_fractions=fractions,noise_sha256=sha256(raw),noise_csv_sha256=sha256(path))
    return result


def characterize(args,site,fresh):
    nominal = read_json(args.nominal/"summary.json")
    if nominal["baseline_sha256"]!=sha256(args.matching/"sensitivity.json") or nominal["bank"] not in BANKS.values():
        raise ValueError("Expected the audited nominal code experiment")
    _,sizes,base = load_design(args.ota,args.cmfb,site)
    bank,code = nominal["bank"],nominal["fixed_code"]
    original,area = physical_bank(args.nominal/"oa",site,sizes,base,False,bank)
    folder = args.nominal/("oa" if code==bank["code"] else "fixed-code-oa")
    fixed,actual = physical_bank(folder,site,sizes,base,False,bank,code)
    if area!=actual or area!=nominal["budget"] or hierarchy(applied(original,code))!=hierarchy(fixed):
        raise ValueError("Wrong native bench, trim word or geometry for the bias-matched measurement")
    if fresh:
        args.out.mkdir(parents=True)
    operating = {}
    for state in STATES:
        for label,icm in [("nominal",.6*state[2])]+[(str(mv),mv/1000) for mv in range(950,1101,15)]:
            key = state[0]+"-"+label
            operating[key] = station(args.out/key,fixed,state,site,icm,label=="nominal",fresh)
        print(state[0],"bias-matched gain, noise",operating[state[0]+"-nominal"]["gain_dB"],
              operating[state[0]+"-nominal"]["noise_uVrms"],flush=True)
    grid = joint_grid(args.out,fixed,site,fresh)
    rows = [r for points in grid.values() for r in points]
    result = dict(nominal_sha256=sha256(args.nominal/"summary.json"),fixed_code=code,operating=operating,
                  grid=grid,grid_passed=sum(r["passed"] for r in rows),grid_total=len(rows))
    persist(args.out/"summary.json",result,fresh)
    return result


if __name__=="__main__":
    options,settings = arguments(__doc__,with_nominal=True)
    report = characterize(options,settings,options.action=="run")
    print("Nominal fixed-code joint grid",report["grid_passed"],"/",report["grid_total"])
