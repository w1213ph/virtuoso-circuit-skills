"""Size the CMOS gain cell and measure the actual receiving port in all four modes."""
import json
import math

from mos_table import table


GRID = 41
CODES = ("00", "01", "10", "11")
OP = ("v(vdd)", "v(vin)", "v(xrx.bias)", "v(xpga.sum)", "v(drive)", "v(rxin)", "v(xrx.mon)",
      "v(b0)", "v(b1)", "v(xpga.xsw0.enb)", "v(xpga.xsw1.enb)", "i(vsup)")
AC = ("vin_r", "vin_i", "drive_r", "drive_i", "rxin_r", "rxin_i")
TRANSIENT = ("v(vin)", "v(drive)", "v(rxin)", "v(b0)", "v(b1)")
FIELDS = ("low_Hz", "high_Hz", "max_gain_error_dB", "max_supply_uA", "input_common_mode_fraction",
          "receiver_cap_pF", "transition_input_offset_mV", "settling_band_mV", "max_settling_ns")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def check(ok, message):
    if not ok:
        raise ValueError(message)


def positive(x):
    return type(x) in (float, int) and 0 < x < float("inf")


def requirements(path):
    r = read_json(path)
    check(isinstance(r, dict) and set(r) == set(FIELDS) | {"gain_targets_V_V", "purpose"},
          "Supply all fields from requirements.example.json with the documented units")
    check(isinstance(r["purpose"], str) and r["purpose"].strip(), "Describe the intended use")
    targets = r["gain_targets_V_V"]
    check(isinstance(targets, list) and len(targets) == 4 and all(positive(x) for x in targets),
          "Supply four positive target magnitudes in B1 B0 order")
    check(all(positive(r[k]) for k in FIELDS), "All electrical requirements must be finite positive numbers")
    check(r["low_Hz"] < r["high_Hz"], "The AC band must have two different ordered endpoints")
    check(r["input_common_mode_fraction"] + r["transition_input_offset_mV"] / 1620 < 1,
          "Both input voltages must fit the lowest supply")
    check(r["max_settling_ns"] <= 2900, "The transient fixture observes each final state for 2.9 us")
    return r


def design(rows, r):
    # Use the same measured drain/body bias for the two current-density estimates.
    matches = [v for v in rows if v["length_um"] == 1 and abs(v["vds_V"]) == .65 and v["vbs_V"] == 0]
    bias = 1.8 * r["input_common_mode_fraction"]
    def nearest(pol, gate):
        subset = [v for v in matches if v["polarity"] == pol and v["gm_id_per_V"]]
        check(subset, "Missing measured TT gm/ID data for "+pol)
        return min(subset, key=lambda v: abs(v["abs_vgs_V"]-gate))
    n, p = nearest("N", bias), nearest("P", 1.8-bias)
    wn = 2.0
    wp = wn*n["id_per_width_uA_um"]/p["id_per_width_uA_um"]
    p_units = max(1, math.ceil(wp/6))
    wp_unit = round(wp/p_units, 1)
    check(wp_unit >= .42 and p_units <= 64, "Bias is outside the supported inverter sizing family")
    unit_gm = (wn*n["id_per_width_uA_um"]*n["gm_id_per_V"] +
               wp_unit*p_units*p["id_per_width_uA_um"]*p["gm_id_per_V"])
    gm_floor = 2*math.pi*r["high_Hz"]*r["receiver_cap_pF"]*1e-6*(1+max(r["gain_targets_V_V"]))
    base = 2**max(0, math.ceil(math.log2(2*gm_floor/unit_gm)))
    check(base <= 64, "Required drive exceeds the supported parallel-unit search")
    return {"tt_nmos": n, "tt_pmos": p, "n_width_um": wn, "p_width_um": wp_unit, "p_units": p_units,
            "unit_gm_uS": unit_gm, "gm_floor_uS": gm_floor, "baseline": base, "candidate": 4*base}


def operating_point(path, code, supply, vin):
    with path.open(encoding="utf-8") as source:
        lines = [s.split() for s in source if s.strip()]
    check(len(lines) == 2 and tuple(s.lower() for s in lines[0][1:]) == OP and len(lines[1]) == 1+len(OP),
          "Incomplete DC operating point in "+str(path))
    vals = dict(zip(OP, map(float, lines[1][1:])))
    check(all(math.isfinite(v) for v in vals.values()), "Nonfinite operating point")
    check(abs(vals["v(vdd)"]-supply) < 1e-6 and abs(vals["v(vin)"]-vin) < 1e-6, "Unexpected supply or input bias")
    for bit, digit in enumerate(reversed(code)):
        v, vb = vals["v(b%d)"%bit], vals["v(xpga.xsw%d.enb)"%bit]
        check(abs(v-int(digit)*supply) < .02*supply and abs(vb-(1-int(digit))*supply) < .02*supply,
              "Native transmission gate controls disagree with code "+code)
    return vals


def static_metrics(folder, r, code, supply):
    vin = supply*r["input_common_mode_fraction"]
    op = operating_point(folder/"op.dat", code, supply, vin)
    step = operating_point(folder/"offset.dat", code, supply, vin + r["transition_input_offset_mV"]/1000)
    rows = table(folder/"ac.dat", AC)
    check(len(rows) == GRID, "Incomplete gain sweep")
    rx, drive = [], []
    for i, (f, ir, ii, dr, di, rr, ri) in enumerate(rows):
        freq = r["low_Hz"]+(r["high_Hz"]-r["low_Hz"])*i/(GRID-1)
        check(math.isclose(f, freq, rel_tol=1e-9) and abs(complex(ir, ii)-1) < 1e-6,
              "Unexpected AC frequency or input phasor")
        h = complex(rr, ri)/complex(ir, ii)
        internal = complex(dr, di)/complex(ir, ii)
        check(abs(h) > 0 and abs(internal) > 0, "Missing AC response")
        if i == 0:
            # An inverted nominal response has a negative real component.
            polarity = h.real < 0
        rx.append(20*math.log10(abs(h)))
        drive.append(20*math.log10(abs(internal)))
    target = 20*math.log10(r["gain_targets_V_V"][int(code,2)])
    worst = max(range(GRID), key=lambda i: abs(rx[i]-target))
    return {"gain_low_dB":rx[0], "gain_high_dB":rx[-1], "drive_high_dB":drive[-1],
            "max_gain_error_dB":abs(rx[worst]-target), "worst_Hz":rows[worst][0], "inverting":int(polarity),
            "supply_uA":-1e6*op["i(vsup)"], "rx_dc_V":op["v(rxin)"], "bias_V":op["v(xrx.bias)"],
            "offset_rx_V":step["v(rxin)"], "gate_controls_V":[[op["v(b%d)"%i],op["v(xpga.xsw%d.enb)"%i]] for i in (0,1)]}


def switching(folder, r, supply, endpoints):
    rows = table(folder/"transient.dat", TRANSIENT)
    check(rows[0][0] <= 2e-9 and rows[-1][0] >= 7e-6*(1-1e-6), "Incomplete switching transient")
    vin = supply*r["input_common_mode_fraction"] + r["transition_input_offset_mV"]/1000
    check(all(abs(row[1]-vin) < 1e-6 for row in rows), "Unexpected input during switching")
    check(max(b[0]-a[0] for a,b in zip(rows, rows[1:])) <= 2.01e-9, "Transient sampling too coarse")
    edges = []
    for k in (4,5):
        crossings = []
        for a,b in zip(rows, rows[1:]):
            if (a[k] < supply/2) != (b[k] < supply/2):
                crossings.append((a[0]+(supply/2-a[k])*(b[0]-a[0])/(b[k]-a[k]), b[k] > a[k]))
        check([up for t,up in crossings] == [True,False] and
              all(abs(t-scheduled) < 1e-9 for (t,up),scheduled in zip(crossings,(1.001e-6,4.001e-6))),
              "Expected two measured control transitions on each bit")
        edges.append(crossings)
    band = r["settling_band_mV"]/1000
    check(abs(endpoints["00"]-endpoints["11"]) > 2*band, "Output step does not resolve the declared settling band")
    check(abs(rows[0][3]-endpoints["00"]) <= band, "Initial loaded output differs from its independent DC reference")
    results = {}
    for j, (code, stop) in enumerate((("11",3.999e-6),("00",7e-6))):
        start = max(pair[j][0] for pair in edges)
        samples = [(t,v) for t,_,_,v,_,_ in rows if start <= t <= stop*(1+1e-9)]
        for col in (4,5):
            settled_bits = [row[col] for row in rows if start+3e-9 <= row[0] <= stop]
            check(all(abs(v-(supply if code=="11" else 0)) < .02*supply for v in settled_bits), "Incorrect final bit level")
        last = max((i for i,(_,v) in enumerate(samples) if abs(v-endpoints[code]) > band), default=-1)
        # First sampled time after the last excursion is a conservative upper bound.
        value = (samples[last+1][0]-start)*1e9 if last+1 < len(samples) else None
        results[code] = {"settling_ns":value,"reference_V":endpoints[code],"control_crossing_ns":start*1e9}
    return results


def deck(native, lib, state, r, code, transient=False):
    corner,temp,vd = state
    vin = vd*r["input_common_mode_fraction"]
    offset = vin+r["transition_input_offset_mV"]/1000
    lines = ["Programmable gain with receiving load", '.lib "{}" {}'.format(lib,corner), ".temp "+str(temp),
             ".param VSUPPLY={:.15g} VINBIAS={:.15g} BIT0={} BIT1={}".format(vd,offset if transient else vin,code[1],code[0]),
             native, ".control", "set wr_vecnames", "set wr_singlescale", "set numdgt=12"]
    if transient:
        lines += ["tran 2n 7u 0 2n", "wrdata transient.dat "+" ".join(TRANSIENT)]
    else:
        lines += ["op","wrdata op.dat "+" ".join(OP),
                  "ac lin {} {} {}".format(GRID,r["low_Hz"],r["high_Hz"])]
        for node in ("vin","drive","rxin"):
            lines += ["let {0}_r = real(v({0}))".format(node), "let {0}_i = imag(v({0}))".format(node)]
        lines += ["wrdata ac.dat "+" ".join(AC), "alter vsig dc={:.15g}".format(offset),
                  "op","wrdata offset.dat "+" ".join(OP)]
    return "\n".join(lines+["echo analysis_complete","quit",".endc",".end",""])
