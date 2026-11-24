"""Run one Spectre analysis and record the files the simulator actually loaded."""

import hashlib
import json
import os
import re
import signal
import subprocess
from pathlib import Path


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path, value):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def execute(argv, directory, console, timeout):
    with (directory / console).open("wb") as output:
        with subprocess.Popen([str(arg) for arg in argv], cwd=str(directory), stdout=output,
                              stderr=subprocess.STDOUT, start_new_session=True) as job:
            try:
                code = job.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                # Native launchers can spawn children. Only stop this invocation's process group.
                try:
                    os.killpg(job.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                job.wait()
                raise RuntimeError("{} timed out. See {}".format(Path(argv[0]).name, directory / console))
            if code:
                raise RuntimeError("{} exited {}. See {}".format(Path(argv[0]).name, code, directory / console))


def run_spectre(tool, folder, model, traces):
    execute([tool, "-64", "+mt=1", "input.scs", "-format", "psfascii", "-raw", "psf",
             "+log", "spectre.out"], folder, "console.out", 180)
    log = (folder / "spectre.out").read_text(encoding="utf-8", errors="replace")
    summary = re.search(r"spectre completes with (\d+) errors?, (\d+) warnings?", log, re.I)
    if not summary or int(summary[1]):
        raise ValueError("Spectre did not complete cleanly. See " + str(folder / "spectre.out"))
    primary = (folder / "input.scs").resolve()
    dependencies = {}
    for name in re.findall(r"(?m)^Reading file:\s*([^\n]+)$", log):
        path = (folder / name.strip()).resolve()
        if path != primary:
            dependencies[str(path)] = sha256(path)
    if str(model) not in dependencies:
        raise ValueError("Configured model is missing from the Spectre readback")
    outputs = [folder / "psf" / name for name in traces if (folder / "psf" / name).is_file()]
    if len(outputs) != 1 or not outputs[0].stat().st_size:
        raise ValueError("Expected one nonempty PSF ASCII analysis in " + str(folder))
    return outputs[0], dependencies, int(summary[2])


TRANSIENT = """simulator lang=spectre
global 0
parameters VDDVAL={vdd_V:g} VCM={vcm_V:g} VDIFF={delta_mV:g}m RSRC=1k CLVAL={cload_fF:g}f TRSUP={ramp_ns:g}n
include "{model}" section={corner}
simulator lang=spectre
{native}
opt options temp={temp_C:g} reltol=1e-5 vabstol=1e-7 iabstol=1e-13
saveOptions options save=selected
save VDD CLK INP INN SRC_P SRC_N OUTP OUTN VSUP:p VCLK:p XDUT.XP XDUT.XN XDUT.TAIL
tran tran stop=39n maxstep=5p strobeperiod=5p strobeoutput=strobeonly errpreset=conservative
"""
