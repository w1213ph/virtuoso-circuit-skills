"""Exercise the upstream slow-corner mismatch coefficients on the frozen sampling driver."""
import math
import random
import statistics

from bench import REF_ID, budget, deck, transient_metrics
from driver import OTA, SAMPLER
from mos_table import STATES, table


SLOW = next(state for state in STATES if state[0] == "ss")
DEVICES = dict(OTA, **SAMPLER)
KNOBS = {"n": ("toxe", "vth0", "voff"), "p": ("toxe", "vth0", "voff", "nfactor")}
METRICS = ("up_track_mV", "up_hold_mV", "down_track_mV", "down_hold_mV")


def require(ok, message):
    if not ok:
        raise ValueError(message)


def draw(seed):
    """One independent normal draw per local parameter of each physical instance group."""
    rng = random.Random(0 if seed is None else seed)
    return {name: {k: float(format(rng.gauss(0, 1), ".12g")) if seed is not None else 0
                   for k in KNOBS[DEVICES[name][1]]} for name in sorted(DEVICES)}


def seed_range(first, count):
    require(type(first) is int and type(count) is int and 2<=count<=256 and 0<=first<=2**31-count,
            "Use 2–256 samples and a nonnegative 31-bit seed range")
    return range(first, first+count)


def trial_names(first, count):
    seeds = seed_range(first, count)
    return [("reference",None,None),("zero",None,draw(None)),
            *[("seed-"+str(s),s,draw(s)) for s in seeds], ("repeat",first,draw(first))]


def wrapper(name, model, geometry, values):
    # M scales the DC branch current in ngspice. The upstream card uses a
    # separate mult parameter in its local mismatch equations, sqrt(L*W*mult).
    mult, = [kv.split("=")[1] for kv in geometry.split() if kv.lower().startswith("m=")]
    return [
        ".subckt "+name+" d g s b",
        *[".param {}__{}_slope_spectre={:.12g}".format(model, k, v) for k, v in values.items()],
        "Xdevice d g s b {} {} mult={}".format(model, geometry, mult), ".ends "+name]


def wrap_native(native, values):
    seen, definitions, body = set(), [], []
    for line in native.splitlines():
        words = line.split(None, 6)
        if words and words[0] in DEVICES:
            name, d, g, s, b, model, geometry = words
            require(name not in seen and model.lower() == "sky130_fd_pr__"+DEVICES[name][1]+"fet_01v8",
                    "Unexpected MOS instance " + name)
            seen.add(name)
            label = "MM_"+name
            definitions.extend(wrapper(label, model, geometry, values[name]))
            line = " ".join((name, d, g, s, b, label))
        body.append(line)
    require(seen == set(DEVICES) and body[-1].strip().lower()==".end", "Incomplete native driver hierarchy")
    return "\n".join(body[:-1]+definitions+[".end", ""])


def sample_deck(native, lib, spec, values):
    if values is not None:
        native = wrap_native(native, values)
    text = deck(native, lib, SLOW, spec, True)
    if values is not None:
        text = text.replace(REF_ID, REF_ID.replace(".xnref.m", ".xnref.xdevice.m"))
    mid = (spec["input_low_V"] + spec["input_high_V"])/2
    span = min(.04, mid/2, (SLOW[2]-mid)/2)
    sweep = "dc VDATA {:.12g} {:.12g} {:.12g}\nwrdata balance.dat v(vin) v(out) v(fb)\n".format(
        mid-span, mid+span, span/40)
    return text.replace("ac dec 60 10 10G", sweep+"ac dec 60 10 10G")


def pair_deck(lib, sizing):
    lines = ["Slow-corner matched pairs", '.lib "{}" ss'.format(lib), ".temp 85",
             "VDD vdd 0 1.62", "VGN gn 0 .75", "VGP gp 0 .77"]
    vectors = []
    for pol, instance in (("n", "XIP"), ("p", "XLA")):
        w, l, count = sizing["devices"][instance]
        model = "sky130_fd_pr__"+pol+"fet_01v8"
        source = "0" if pol == "n" else "vdd"
        for mode, signs in (("zero", (0,0)), ("split",(1,-1)), ("swap",(-1,1)), ("unit",(1,-1))):
            for i, sign in enumerate(signs):
                key = "{}{}{}".format(pol, mode, i)
                geometry = "w={} l={} m={}".format(w, l, 1 if mode=="unit" else count)
                lines.extend(wrapper("pair_"+key, model, geometry, {"vth0": sign}))
                lines.extend(["V{} d{} 0 {}".format(key, key, .65 if pol=="n" else .97),
                              "X{} d{} g{} {} {} pair_{}".format(key,key,pol,source,source,key)])
                vectors.append("i(v"+key+")")
    return "\n".join(lines+[".control","set wr_singlescale","set wr_vecnames","set numdgt=14","op",
                            "wrdata pair.dat "+" ".join(vectors),"echo analysis_complete","quit",".endc",".end",""])


def pair_response(path, sizing):
    rows = [line.split() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    names = ["i(v{}{}{})".format(pol,mode,i) for pol in ("n","p") for mode in ("zero","split","swap","unit") for i in (0,1)]
    require(len(rows)==2 and [x.lower() for x in rows[0][1:]]==names and len(rows[1])==len(names)+1,
            "Incomplete matched-pair calibration")
    vals = dict(zip(names, map(float,rows[1][1:])))
    require(all(math.isfinite(v) and abs(v)>1e-10 for v in vals.values()),"Matched pair is off or unreadable")
    response = {}
    for pol,instance in (("n","XIP"),("p","XLA")):
        def relative(mode):
            a,b = [abs(vals["i(v{}{}{})".format(pol,mode,i)]) for i in (0,1)]
            return 2*(a-b)/(a+b)
        off,split,swap,unit = map(relative, ("zero","split","swap","unit"))
        require(abs(off)<1e-7 and abs(split)>1e-5 and math.isclose(split,-swap,rel_tol=1e-4),
                "Unresolved local mismatch or a shared perturbation for "+pol)
        ratio = unit/split
        require(math.isclose(ratio, math.sqrt(sizing["devices"][instance][2]), rel_tol=.08),
                "Area scaling does not match the native parallel device count for "+pol)
        response[pol] = {"zero_difference":off, "opposite_vth_difference":split,
                         "swapped_difference":swap, "unit_to_native_ratio":ratio}
    return response


def offset(path, spec):
    """VIN minus FB at balanced output, measured with the nominal feedback and track clock."""
    rows = table(path, ("v(vin)","v(out)","v(fb)"))
    mid = (spec["input_low_V"]+spec["input_high_V"])/2
    span = min(.04, mid/2, (SLOW[2]-mid)/2)
    require(len(rows)==81 and all(abs(v-(mid-span+i*span/40))<1e-7 and abs(v-x)<1e-7 and abs(fb-y)<1e-7
            for i,(x,v,y,fb) in enumerate(rows)),"Incorrect input balance sweep or feedback connection")
    require(all(b[2]>a[2] for a,b in zip(rows,rows[1:])),"Nonmonotone DC response at the specified common mode")
    for a,b in zip(rows,rows[1:]):
        if a[2]<=mid<=b[2]:
            fraction = (mid-a[2])/(b[2]-a[2])
            return 1000*(a[1]+fraction*(b[1]-a[1])-mid)
    raise ValueError("DC sweep does not bracket the specified output")


def acquisition(folder, spec):
    data = transient_metrics(folder, spec, SLOW[2])["edges"]
    return {edge+"_"+phase+"_mV":data[edge][phase+"_error_mV"] for edge in ("up","down") for phase in ("track","hold")}


def measured(folder, spec):
    values = acquisition(folder, spec)
    values["input_offset_mV"] = offset(folder/"balance.dat", spec)
    return values


def distribution(values):
    return {"min":min(values), "mean":statistics.mean(values), "sample_sd":statistics.stdev(values), "max":max(values)}


def summarize(samples, nominal, spec):
    limit = budget(spec)["half_lsb_mV"]
    require(len(samples)>=2 and len({r["seed"] for r in samples})==len(samples),"Repeated or missing sample seeds")
    require(max(r["input_offset_mV"] for r in samples)-min(r["input_offset_mV"] for r in samples)>.001,
            "The full circuit did not show a resolved change in input-referred offset")
    count = sum(all(r[k]<=limit for k in METRICS) for r in samples)
    return {"status":"sample_limits_met" if count==len(samples) else "sample_limits_exceeded",
            "samples":len(samples), "within_all_four":count, "half_lsb_mV":limit, "nominal":nominal,
            "within_each":{k:sum(r[k]<=limit for r in samples) for k in METRICS},
            "spread":{k:distribution([r[k] for r in samples]) for k in (*METRICS,"input_offset_mV")},
            "offset_shift_mV":distribution([r["input_offset_mV"]-nominal["input_offset_mV"] for r in samples])}
