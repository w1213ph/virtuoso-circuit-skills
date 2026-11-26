"""Calibrate spectral replay with an ngspice thermal RC and its finite-band analytic integral."""
import argparse
import csv
import math
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"skills/paper-to-virtuoso/scripts"))
from check_prelayout import band_noise_rms


def theory(resistance, capacitance, kelvin, low, high):
    pole = 1 / (2 * math.pi * resistance * capacitance)
    variance = 4 * 1.380649e-23 * kelvin * resistance * pole * (math.atan(high/pole) - math.atan(low/pole))
    return math.sqrt(variance)


def calibrate(tool, directory, resistance, capacitance, celsius, band):
    netlist = f"""Thermal noise of a resistor driving a shunt capacitor
Vdrive src 0 dc 0 ac 1
Rnoise src out {resistance:.12g}
Cload out 0 {capacitance:.12g}
.temp {celsius:g}
.control
set numdgt=16
set wr_singlescale
unset sqrnoise
noise v(out) Vdrive dec 150 1 10Meg
setplot noise1
wrdata noise.dat onoise_spectrum
setplot noise2
print onoise_total
quit
.endc
.end
"""
    (directory/"rc.cir").write_text(netlist,encoding="ascii")
    run = subprocess.run([str(tool),"-n","-b","rc.cir"],cwd=directory,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                         universal_newlines=True,timeout=30)
    run.check_returncode()
    total = re.search(r"(?im)^onoise_total\s*=\s*([0-9.e+-]+)",run.stdout)
    if not total:
        raise ValueError("ngspice did not report integrated output voltage noise")
    path = directory/"spectrum.csv"
    with path.open("w",newline="",encoding="ascii") as stream:
        writer = csv.writer(stream)
        writer.writerow(("frequency_Hz","out_V_per_sqrt_Hz"))
        for line in (directory/"noise.dat").read_text().splitlines():
            freq, asd = map(float,line.split())
            writer.writerow((freq,asd))
    spec = dict(method="band_noise_rms", axis="frequency_Hz", density="out_V_per_sqrt_Hz", density_unit="V/sqrt(Hz)",
                file=path.name, band_Hz=band)
    rms, details = band_noise_rms(path,spec,"V RMS")
    analytic = theory(resistance,capacitance,celsius+273.15,*band)
    full = theory(resistance,capacitance,celsius+273.15,1,1e7)
    if not math.isclose(float(total.group(1)),full,rel_tol=.001) or not math.isclose(rms,analytic,rel_tol=.001):
        raise ValueError("Measured noise disagrees with the RC thermal-noise integral by more than 0.1 percent")
    return rms, analytic, details["spectrum_points"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ngspice",default="ngspice")
    args = parser.parse_args()
    for r,c,temp,band in ((10000,100e-12,27,[20,3e5]),(3300,3e-12,85,[1200,2e6])):
        with tempfile.TemporaryDirectory() as name:
            rms, expected, points = calibrate(args.ngspice,Path(name),r,c,temp,band)
        print(f"{r:g} ohm, {c*1e12:g} pF, {temp} C, {band} Hz, {points} points: "
              f"measured {rms*1e6:.6f} uV RMS, theory {expected*1e6:.6f} uV RMS")


if __name__=="__main__":
    main()
