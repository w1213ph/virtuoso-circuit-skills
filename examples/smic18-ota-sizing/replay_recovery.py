"""Recompute a slow-corner settling failure and its repair using the published samples."""
import argparse
import csv
import math
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parents[1]/"skills/paper-to-virtuoso/scripts"))
from settling_diagnosis import settling_window


def compare(path):
    with path.open(encoding="utf8",newline="") as stream:
        data = csv.DictReader(stream)
        if data.fieldnames!=["time_ns","baseline_diff_mV","recovery_diff_mV"]:
            raise ValueError("Expected time and two differential outputs in ns and mV")
        rows = [tuple(float(row[k]) for k in data.fieldnames) for row in data]
    if len(rows)!=1999 or any(not math.isclose(t,-1+2*i,rel_tol=0,abs_tol=1e-5) for i,(t,_,_) in enumerate(rows)):
        raise ValueError("Expected every 2 ns sample from 1 ns before the edge through the end of the 3995 ns window")
    time = [r[0]*1e-9 for r in rows]
    return {name:settling_window(time,[row[i] for row in rows],-300,1,0,3995e-9)
            for i,name in ((1,"baseline"),(2,"recovery"))}


if __name__=="__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("samples",type=Path,nargs="?",default=HERE/"recovery-waves.csv")
    for name,result in compare(parser.parse_args().samples).items():
        def microseconds(value):
            return "never" if value is None else "{:.3f} us".format(value*1e6)
        print("{}: first {}, permanent {}, exits {}, peak after entry {:.3f} mV".format(
            name,microseconds(result["first_entry_s"]),microseconds(result["permanent_entry_s"]),
            result["exits_after_entry"],result["max_error_after_first"])
            if result["first_entry_s"] is not None else name+": never entered the tolerance band")
