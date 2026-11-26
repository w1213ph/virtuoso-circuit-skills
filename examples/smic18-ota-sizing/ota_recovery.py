"""Compare differential settling and common-mode feedback repairs on the same physical OTA."""
import argparse
from pathlib import Path
from types import SimpleNamespace

from ota import config_for_site
from ota_cmfb import ICM, checked_trace, exact_swing, persist
from ota_matching import fullscale_copy
from ota_measure import MODES, deck, samples, unity
from ota_robust import fullscale_delivery, fullscale_metrics, fullscale_source, load_design
from ota_trim import BANKS, GEOMETRY, characterize, physical_bank
from ota_trim_ac import characterize as characterize_ac
from ota_trim_stats import experiment
from bias_sim import STATES
from spectre_run import read_json, sha256


DESIGNS = {"cap":dict(plate_um=26.5),
           **{"r"+str(k):dict(plate_um=26.5,cm_bridge_ohm=k*1000) for k in (130,150,220,390)}}
SENSE_SIZES = {"sense-w050-l375":(.50,3.75),"sense-w045-l350":(.45,3.50),"sense-w050-l350":(.50,3.50)}
DESIGNS.update({name:dict(DESIGNS["r130"],cm_input=w,cm_length_um=l) for name,(w,l) in SENSE_SIZES.items()})
BIAS_WIDTHS = {"sense-bias140": 1.40, "sense-bias145": 1.45}
DESIGNS.update({name:dict(DESIGNS["sense-w050-l350"],bias_ref_width_um=w) for name,w in BIAS_WIDTHS.items()})


def screen(args,site,folder,design,fresh):
    _,sizes,reference = load_design(args.ota,args.cmfb,site)
    original = read_json(args.nominal/"summary.json")
    bank = BANKS["centered"]
    if original["bank"]!=bank or original["fixed_code"]!=7 or original.get("recovery"):
        raise ValueError("Screening requires the original centered trim bank and its measured fixed code")
    if fresh:
        folder.mkdir(parents=True)
    net,area = physical_bank(folder/"oa",site,sizes,reference,fresh,bank,original["fixed_code"],design)
    rows = {}
    for state in STATES:
        s = state[0]
        pms = {}
        for mode in ("dm","cm"):
            raw = checked_trace(folder/(s+"-"+mode),deck(site,state,net,mode),MODES[mode][1],site,fresh)
            values = samples(raw,"freq",("loopGain",))
            pms[mode] = unity(values["freq"],[-v for v in values["loopGain"]])["phase_margin_deg"]
        for icm in ICM:
            key = "{}-{}".format(s,round(1000*icm))
            dc = exact_swing(folder/(key+"-dc"),net,state,site,fresh,icm)
            raw = checked_trace(folder/(key+"-large"),fullscale_source(fullscale_copy(net),site,state,icm),
                                "tran.tran",site,fresh)
            wave = fullscale_metrics(raw,state[2],icm)
            rows[key] = dict(phase_margin_deg=pms,static=dc,wave=wave,raw_sha256=sha256(raw))
    result = dict(design=design,area=area,conditions=rows,source_sha256=sha256(args.nominal/"summary.json"))
    persist(folder/"summary.json",result,fresh)
    return result


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("run","verify"))
    parser.add_argument("--stage",choices=("screen","nominal","operating","statistics","handoff"),required=True)
    parser.add_argument("--design",choices=DESIGNS,required=True)
    for key in ("config","ota","cmfb","matching","nominal","out"):
        parser.add_argument("--"+key,type=Path,required=True)
    args = parser.parse_args()
    site = config_for_site(args.config)
    site["model_sha256"] = sha256(site["model_file"])
    site["model_file"] = str(site["model_file"])
    return args,site


def main():
    args,site = arguments()
    fresh = args.action=="run"
    folder = args.out/(args.design+"-"+args.stage)
    design = DESIGNS[args.design]
    if args.stage=="screen":
        report = screen(args,site,folder,design,fresh)
        for name,row in report["conditions"].items():
            print(name,row["phase_margin_deg"],[p["joint_settling_us"] for p in row["wave"]["directions"].values()])
        return
    source = args.out/(args.design+"-screen/summary.json")
    previous = read_json(source)
    if previous["design"]!=design or previous["source_sha256"]!=sha256(args.nominal/"summary.json"):
        raise ValueError("Screen evidence refers to another circuit")
    spec = SimpleNamespace(**vars(args))
    spec.out,spec.bank = folder,"centered"
    if args.stage=="nominal":
        result = characterize(spec,site,fresh,design)
        if previous["area"]!=result["budget"]:
            raise ValueError("Screen and nominal passives or MOS dimensions differ")
    else:
        spec.nominal = args.out/(args.design+"-nominal")
        nominal = read_json(spec.nominal/"summary.json")
        if nominal.get("recovery")!=design:
            raise ValueError("The nominal study differs from the requested drawing")
        if args.stage=="operating":
            characterize_ac(spec,site,fresh)
        elif args.stage=="statistics":
            experiment(spec,site,fresh)
        else:
            if fresh:
                folder.mkdir(parents=True)
            _,sizes,reference = load_design(args.ota,args.cmfb,site)
            bank,code = nominal["bank"],nominal["fixed_code"]
            source = spec.nominal/("oa" if code==bank["code"] else "fixed-code-oa")
            native,budget = physical_bank(source,site,sizes,reference,False,bank,code,design)
            actual = dict(sizes,paired_geometry=GEOMETRY,source_trim=dict(bank,code=code),**design)
            waves,history = fullscale_delivery(folder,native,actual,budget["physical"],site,fresh)
            for key,raw in waves.items():
                previous = nominal["fullscale"][key]
                if any(value!=previous[k] for k,value in raw.items() if k!="raw_sha256"):
                    raise ValueError("Native full-scale testbench differs from the qualified full-scale waveforms")
            persist(folder/"summary.json",dict(nominal_sha256=sha256(spec.nominal/"summary.json"),fullscale=waves,maestro=history),fresh)


if __name__=="__main__":
    main()
