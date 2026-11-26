"""Run the same frozen acceptance cases against a current or historical checker."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT/"skills/paper-to-virtuoso/scripts/check_prelayout.py"
FIXTURES = ROOT/"tests/fixtures/noise-replay-cases.json"


def invoke(script, action, path):
    job = subprocess.run([sys.executable,"-IB",str(script),action,"--plan",str(path)],capture_output=True)
    if job.returncode not in (0,1):
        raise ValueError(job.stderr.decode(errors="replace"))
    return json.loads(job.stdout)


def run_case(checker, case, folder):
    path = folder/"plan.json"
    (folder/"dut.txt").write_text("held-out linear fixture",encoding="utf8")
    (folder/"spectrum.csv").write_bytes(case["spectrum"].encode())
    plan = dict(schema_version=1,inputs=["dut.txt"],tests=[case["test"]],results={})
    path.write_text(json.dumps(plan),encoding="utf8")
    signature = invoke(checker,"snapshot",path)["tests"]["x"]["context_sha256"]
    if case["reported"] is not None:
        evidence = {name:hashlib.sha256((folder/name).read_bytes()).hexdigest() for name in case["evidence"]}
        plan["results"]["x"] = dict(status="measured",unit=case["test"]["unit"],context_sha256=signature,
                                    value=case["reported"],evidence=evidence)
        path.write_text(json.dumps(plan),encoding="utf8")
    status = invoke(checker,"check",path)["tests"][0]["status"]
    outcome = status if status in ("PASS","FAIL","NOT_RUN") else "BLOCKED"
    return dict(name=case["name"],expected=case["expected"],actual=status,matched=outcome==case["expected"])


def benchmark(checker):
    cases = json.loads(FIXTURES.read_bytes())
    reports = []
    for case in cases:
        with tempfile.TemporaryDirectory() as folder:
            reports.append(run_case(checker,case,Path(folder)))
    return reports


if __name__=="__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checker",type=Path,default=CHECKER)
    results = benchmark(parser.parse_args().checker.resolve())
    print(json.dumps(dict(matched=sum(x["matched"] for x in results),total=len(results),cases=results),indent=2))
    sys.exit(0 if all(x["matched"] for x in results) else 1)
