"""Plot the two measured curves that determine the delivered mirror's decision."""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import transfer


def draw(out, process, name, case, req, destination):
    dc_corner, tran_corner = transfer.search.worst_traces(case)
    base = out / process / name
    columns = transfer.bench.measure.VECTORS + ("v(vdd)",)
    sweep = transfer.mos_table.table(base / dc_corner / "dc.dat", columns)
    tran = transfer.mos_table.table(base / tran_corner / "transient.dat", columns)
    dc = [row for row in sweep if 0.4 <= row[1] <= 1.2]
    ref = 0.99 * req["reference_uA"] * 1e-6
    index = next(i for i, row in enumerate(tran) if -row[4] >= ref)
    left, right = tran[index-1], tran[index]
    onset = left[0] + (ref + left[4]) / (left[4] - right[4]) * (right[0] - left[0])
    times = [(row[0] - onset) * 1e9 for row in tran]
    metrics = case["corners"][tran_corner]["metrics"]
    steady = metrics["output_current_uA_at_0p8V"] * 1e-6
    deadline = req["max_settling_ns"]
    elapsed = metrics["settling_ns"]

    figure, (a, b) = plt.subplots(1, 2, figsize=(9, 3.4), constrained_layout=True)
    a.plot([row[1] for row in dc], [abs(row[3] / row[4] - 1) * 100 for row in dc], color="#185c76", linewidth=2)
    a.axhline(req["max_error_pct"], color="#9b3e22", linestyle="--", label="Error limit")
    a.set(xlabel="Output voltage (V)", ylabel="Mirror error (%)", title="DC accuracy / " + dc_corner.upper(),
          xlim=(0.4, 1.2), ylim=(0, None))
    b.plot(times, [abs(-row[3] / steady - 1) * 100 for row in tran], color="#185c76", linewidth=2)
    b.set_yscale("symlog", linthresh=1)
    b.axhspan(0, 1, alpha=0.18, color="#44865d", label="Within 1% of steady current")
    b.axvline(deadline, color="#9b3e22", linestyle="--", label="Deadline")
    stop = times[-1] if elapsed is None else min(times[-1], 1.3 * max(elapsed, deadline))
    b.set(xlabel="Time since reference reached 99% (ns)", ylabel="Output settling error (%)",
          title="Turn-on at 0.80 V / " + tran_corner.upper(), xlim=(0, stop), ylim=(0, None))
    b.set_yticks([0, 1, 10, 100], labels=["0", "1", "10", "100"])
    for axes in (a, b):
        axes.grid(alpha=0.25)
        axes.legend(loc="best", fontsize=8)
    figure.savefig(destination, dpi=145)
    plt.close(figure)
