"""Measure mirror accuracy and turn-on from the simulator's saved supply, current and voltage data."""
import math
import re

from mos_table import table


DEFAULT_CURRENT_UA = 10.0
OUTPUT_MIN_V = 0.40
OUTPUT_MAX_V = 1.20
MAX_ERROR_PCT = 5.0
MAX_SETTLING_NS = 200
VECTORS = ("v(iout)", "v(iref)", "i(vforce)", "i(vbias)")
CONTROLS = """.control
set wr_singlescale
set wr_vecnames
set numdgt=12
save v(iout) v(iref) i(vforce) i(vbias) v(vdd)
dc VFORCE 0 1.50 0.01
wrdata dc.dat v(iout) v(iref) i(vforce) i(vbias) v(vdd)
tran 0.2n 300n 0 0.2n
wrdata transient.dat v(iout) v(iref) i(vforce) i(vbias) v(vdd)
echo analysis_complete
quit
.endc
.end
"""


def deck(lib, state, native, reference_uA=DEFAULT_CURRENT_UA):
    corner, temp, vdd = state
    body, n = re.subn(r"(?im)^\.end\s*\Z", "", native)
    if n != 1:
        raise ValueError("Native netlist must end in .end")
    return ("SKY130 current mirror from Xschem\n"
            '.lib "{}" {}\n.temp {}\n.param VDDTEST={:g} ISET={:.12g}u VOUTTEST=0.8\n'.format(
                lib, corner, temp, vdd, reference_uA)
            + body + "\n" + CONTROLS)


def metrics(folder, vdd, reference_uA=DEFAULT_CURRENT_UA, max_error_pct=MAX_ERROR_PCT):
    reference_A = reference_uA / 1e6
    dc = table(folder / "dc.dat", VECTORS + ("v(vdd)",))
    if len(dc) != 151 or not math.isclose(dc[-1][0], 1.5):
        raise ValueError("Incomplete output-voltage sweep")
    errors, samples = [], {}
    for idx, (sweep, output, reference, force_i, supply_i, supply) in enumerate(dc):
        if not (math.isclose(sweep, idx/100, abs_tol=1e-9) and math.isclose(output, sweep, abs_tol=1e-8)
                and math.isclose(supply, vdd, abs_tol=1e-8) and math.isclose(-supply_i, reference_A, rel_tol=1e-5)):
            raise ValueError("Measured fixture supply, reference current or output voltage differs")
        current = -force_i
        errors.append(abs(current/(-supply_i) - 1) * 100)
        samples[idx] = (current, reference)
    lo, hi = round(100*OUTPUT_MIN_V), round(100*OUTPUT_MAX_V)
    worst = max(range(lo, hi+1), key=errors.__getitem__)
    last_bad = max((i for i in range(hi+1) if errors[i] > max_error_pct), default=-1)
    compliance = 10*(last_bad+1) if last_bad < hi else None
    slope = (samples[85][0]-samples[75][0]) / 0.10
    if slope <= 0:
        raise ValueError("Unexpected output conductance around 0.8 V")
    tran = table(folder / "transient.dat", VECTORS + ("v(vdd)",))
    if (tran[0][0] > 1e-9 or not math.isclose(tran[-1][0], 300e-9, abs_tol=0.3e-9)
            or any(b[0]-a[0] > 0.201e-9 for a,b in zip(tran,tran[1:]))):
        raise ValueError("Incomplete transient or sampling too coarse")
    if any(not math.isclose(r[1], 0.8, abs_tol=1e-7) or not math.isclose(r[5], vdd, abs_tol=1e-7) for r in tran):
        raise ValueError("Transient fixture differs from its nominal output bias")
    if any(abs(r[4]) > reference_A*0.01 for r in tran if r[0] < 9e-9):
        raise ValueError("Reference was already active before turn-on")
    crossing = next((i for i,r in enumerate(tran) if -r[4] >= 0.99*reference_A), None)
    if crossing is None or crossing == 0 or any(abs(-r[4]/reference_A-1)>0.011 for r in tran[crossing:]):
        raise ValueError("Actual reference pulse did not reach and hold its setting")
    first, second = tran[crossing-1], tran[crossing]
    trip = first[0] + (second[0]-first[0])*(0.99*reference_A+first[4])/(first[4]-second[4])
    steady = samples[80][0]
    if steady <= 0:
        raise ValueError("Invalid steady-state output current")
    last = max((i for i in range(crossing,len(tran)) if abs(-tran[i][3]/steady-1) > 0.01), default=crossing-1)
    if last + 1 >= len(tran):
        raise ValueError("Output did not stay within 1% of its measured DC current")
    settle = (tran[last+1][0] - trip)*1e9
    return {"worst_error_pct":errors[worst], "worst_vout_V":dc[worst][1], "compliance_mV":compliance,
            "settling_ns":settle, "output_current_uA_at_0p8V":steady*1e6, "reference_node_V":samples[80][1],
            "output_resistance_Mohm":1e-6/slope, "dc_points":len(dc), "transient_points":len(tran)}
