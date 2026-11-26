"""Define the acquisition contract and extract aperture, hold and loop metrics from native solver records."""
import bisect
import json
import math

from mos_table import table


FIXED_OUTPUT_PF = 0.60
EDGE_NS = 1.0
STEP_NS = 0.25
REF_ID = "@m.xdrv.xnref.msky130_fd_pr__nfet_01v8[id]"
TRACE = ("v(vin)", "v(out)", "v(sample)", "v(clk)", "v(clkb)", "v(vdd)", "v(xdrv.vb)", "i(vvdd)", "iref")
FIELDS = ("adc_bits", "full_scale_span_V", "input_low_V", "input_high_V", "sample_cap_pF",
          "track_ns", "hold_ns", "min_phase_margin_deg")


def requirements(path):
    r = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(r, dict) or set(r) != set(FIELDS) | {"purpose"}:
        raise ValueError("Supply purpose and all fields from requirements.example.json, with the specified units")
    if not isinstance(r["purpose"], str) or not r["purpose"].strip():
        raise ValueError("Describe the sampling requirement")
    if type(r["adc_bits"]) is not int or not 1 <= r["adc_bits"] <= 20:
        raise ValueError("adc_bits must be an integer from 1 to 20")
    if any(type(r[k]) not in (int, float) or not 0 < r[k] <= 1e6 for k in FIELDS):
        raise ValueError("Electrical requirements must be finite positive numbers within the supported range")
    if not 0 < r["input_low_V"] < r["input_high_V"] < 1.62 or not 0 < r["min_phase_margin_deg"] < 180:
        raise ValueError("Input levels must fit all three supplies and the margin must be between 0 and 180 degrees")
    if not 2 <= r["track_ns"] <= 2000 or not 1 <= r["hold_ns"] <= 2000:
        raise ValueError("This fixture supports track windows of 2–2000 ns and holds of 1–2000 ns")
    budget(r)
    return r


def budget(r):
    half = r["full_scale_span_V"] / 2 ** (r["adc_bits"] + 1)
    step = r["input_high_V"] - r["input_low_V"]
    if not half < step <= r["full_scale_span_V"]:
        raise ValueError("The input step must exceed half an LSB and fit the full-scale span")
    tau = r["track_ns"] / math.log(step / half)
    gm = 1000 * (r["sample_cap_pF"] + FIXED_OUTPUT_PF) / tau
    return {"half_lsb_mV": 1000 * half, "step_V": step, "tau_max_ns": tau,
            "one_pole_bandwidth_MHz": 1000 / (2 * math.pi * tau), "gm_floor_uS": gm,
            "branch_target_uA": 4 * gm / 16}


def schedule(r):
    rise = 800.0
    fall = rise + r["track_ns"]
    next_rise = fall + max(200.0, r["hold_ns"] + 40)
    next_fall = next_rise + r["track_ns"]
    return ((rise, fall), (next_rise, next_fall)), next_fall + EDGE_NS + max(49.0, r["hold_ns"] + 5)


def sources(r):
    (up, down), stop = schedule(r)
    lo, hi = r["input_low_V"], r["input_high_V"]
    gate = [(0, 0), (100, 0), (300, 0), (301, 1), (600, 1), (601, 0)]
    for opening, closing in (up, down):
        gate += [(opening, 0), (opening + EDGE_NS, 1), (closing, 1), (closing + EDGE_NS, 0)]
    gate.append((stop, 0))
    def pwl(points):
        return "PWL(" + " ".join("{:.12g}n {}".format(t, v) for t, v in points) + ")"
    data = [(0, 0), (100, lo), (up[0], lo), (up[0] + EDGE_NS, hi), (down[0], hi), (down[0] + EDGE_NS, lo)]
    pos = [(t, "'VSUP'" if level else "0") for t, level in gate]
    neg = [(t, "'VSUP'" if t and not level else "0") for t, level in gate]
    return {"VVDD": "DC 'VSUP' PWL(0 0 100n 'VSUP')", "VDATA": "DC {:.12g} ".format((lo+hi)/2) + pwl(data),
            "VCLK": "DC 'VSUP*TRACK' " + pwl(pos), "VCLKB": "DC 'VSUP*(1-TRACK)' " + pwl(neg),
            "VPROBE": "DC 0 AC 1"}


def deck(native, lib, state, r, tracked):
    if not native.rstrip().lower().endswith("\n.end"):
        raise ValueError("Incomplete Xschem netlist")
    corner, temp, vdd = state
    body = native.rstrip()[:-4]
    lines = ["ADC driver with a switched sampling capacitor", '.lib "{}" {}'.format(lib, corner), ".temp {}".format(temp),
             ".param VSUP={} TRACK={}".format(vdd, int(tracked)), body, ".control", "set wr_singlescale",
             "set wr_vecnames", "set numdgt=12", "save v(vin) v(out) v(fb) v(sample) v(clk) v(clkb) v(vdd) v(xdrv.vb) i(vvdd) " + REF_ID,
             "op", "wrdata op.dat v(vdd) v(clk) v(clkb) v(vin) v(out) v(xdrv.vb) i(vvdd)",
             "ac dec 60 10 10G", "let loop = -v(out)/v(fb)", "wrdata ac.dat loop"]
    if tracked:
        lines.extend(["tran {0}n {1:.12g}n 0 {0}n".format(STEP_NS, schedule(r)[1]),
                      "let iref = " + REF_ID, "wrdata transient.dat " + " ".join(TRACE)])
    return "\n".join(lines + ["echo analysis_complete", "quit", ".endc", ".end", ""])


def loop_metrics(folder, vdd, tracked, r):
    op = (folder / "op.dat").read_text().lower().splitlines()
    if len(op) != 2 or op[0].split()[1:] != ["v(vdd)", "v(clk)", "v(clkb)", "v(vin)", "v(out)", "v(xdrv.vb)", "i(vvdd)"]:
        raise ValueError("Missing DC operating-point vectors")
    _, supply, clk, clkb, vin, out, bias, current = map(float, op[1].split())
    mid = (r["input_low_V"] + r["input_high_V"]) / 2
    if (not all(math.isfinite(x) for x in (supply,clk,clkb,vin,out,bias,current))
            or max(abs(supply-vdd), abs(clk-vdd*tracked), abs(clkb-vdd*(1-tracked)), abs(vin-mid)) > 1e-6
            or not (0 < out < supply and 0 < bias < supply and current < 0)):
        raise ValueError("AC operating point, clock state or injection bias differs")
    rows = table(folder / "ac.dat", ("loop", "loop"))
    if not math.isclose(rows[0][0], 10) or not math.isclose(rows[-1][0], 1e10) or rows[0][1] <= 1:
        raise ValueError("Loop sweep does not bracket the expected negative-feedback bandwidth")
    crossings, previous_phase, last = [], 0.0, None
    for frequency, real, imag in rows:
        magnitude = math.hypot(real,imag)
        if magnitude <= 0:
            raise ValueError("Zero loop magnitude")
        phase = math.degrees(math.atan2(imag,real))
        phase += 360 * round((previous_phase-phase)/360)
        level = math.log(magnitude)
        if last and (last[1] > 0) != (level > 0):
            fraction = last[1] / (last[1]-level)
            crossings.append((math.exp(last[0] + fraction*(math.log(frequency)-last[0])),
                              180 + last[2] + fraction*(phase-last[2])))
        last = (math.log(frequency), level, phase)
        previous_phase = phase
    if len(crossings) != 1 or last[1] >= 0:
        raise ValueError("Review the loop sweep: expected one descending unity-gain crossing")
    return {"unity_MHz": crossings[0][0]/1e6, "phase_margin_deg": crossings[0][1],
            "dc_gain_dB": 20*math.log10(math.hypot(*rows[0][1:])), "dc_output_V": out,
            "bias_V": bias, "quiescent_uA": -current*1e6, "quiescent_power_uW": -vdd*current*1e6}


def transient_metrics(folder, r, vdd, *, settling_detail=False):
    intervals, stop = schedule(r)
    waves = table(folder / "transient.dat", TRACE)
    times = [x[0]*1e9 for x in waves]
    if times[0] > STEP_NS or abs(times[-1]-stop) > .01 or max(b-a for a,b in zip(times,times[1:])) > 1.01*STEP_NS:
        raise ValueError("Transient missing or too coarse for the acquisition aperture")
    def at(t):
        i = max(1, min(len(times)-1, bisect.bisect_left(times,t)))
        f = (t-times[i-1])/(times[i]-times[i-1])
        return tuple(a+f*(b-a) for a,b in zip(waves[i-1],waves[i]))
    def expected(t):
        def ramp(start):
            return min(1., max(0., (t-start)/EDGE_NS))
        low, high = r["input_low_V"], r["input_high_V"]
        rail = min(1., t/100.)*vdd
        clk = vdd*(ramp(300)-ramp(600)+sum(ramp(a)-ramp(b) for a,b in intervals))
        vin = min(1.,t/100.)*low+(high-low)*(ramp(intervals[0][0])-ramp(intervals[1][0]))
        return vin, clk, rail-clk, rail
    for t,row in zip(times,waves):
        actual = (row[1],row[4],row[5],row[6])
        if max(abs(a-b) for a,b in zip(actual,expected(t))) > 2e-5:
            raise ValueError("Recorded input, supply or complementary clocks differ from the defined stimulus")
    if abs(waves[0][7]) > .05 or not 0.1 < at(300)[7] < vdd or not at(300)[9] > 1e-9:
        raise ValueError("Bias did not start with the supply ramp before precharge")
    half = budget(r)["half_lsb_mV"]/1000
    details = {}
    for name, (a,b), target in zip(("up","down"), intervals, (r["input_high_V"],r["input_low_V"])):
        end = b + EDGE_NS + r["hold_ns"]
        before, after = at(b), at(end)
        during = [(t,w[3]) for t,w in zip(times,waves) if a < t < b] + [(b,before[3])]
        last_bad = max((i for i,(_,v) in enumerate(during) if abs(v-target) > half), default=-1)
        settle = None if last_bad == len(during)-1 else during[last_bad+1][0]-a
        held = [at(b+EDGE_NS)[3], after[3]] + [w[3] for t,w in zip(times,waves) if b+EDGE_NS < t < end]
        details[name] = {"track_error_mV": 1e3*abs(before[3]-target), "driver_error_mV": 1e3*abs(before[2]-target),
                         "switch_drop_mV": 1e3*abs(before[2]-before[3]),
                         "hold_error_mV": 1e3*max(abs(v-target) for v in held),
                         "hold_endpoint_error_mV": 1e3*abs(after[3]-target), "settling_ns": settle}
        if settling_detail:
            from settling_diagnosis import settling_window
            observed = [(a,at(a)[3])]+during
            details[name]["settling_detail"] = settling_window([t*1e-9 for t,_ in observed],
                [v for _,v in observed],target,half,a*1e-9,b*1e-9,1.01*STEP_NS*1e-9)
    return {"edges": details, "precharge_error_mV": 1000*abs(at(599)[3]-r["input_low_V"]),
            "reference_current_uA": at(300)[9]*1e6, "bias_at_300ns_V": at(300)[7], "points": len(waves)}
