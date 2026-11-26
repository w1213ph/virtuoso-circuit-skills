"""Exercise evidence binding, the exact cutoff decision and frozen transfer cases."""
import copy
import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
import benchmark_noise_replay as bench
sys.path.insert(0,str(bench.CHECKER.parent))
import check_prelayout as checks


class NoiseReplay(unittest.TestCase):
    def test_frozen_transfer_cases(self):
        rows = bench.benchmark(bench.CHECKER)
        self.assertEqual(len(rows),12)
        self.assertEqual([r for r in rows if not r["matched"]],[])

    def test_raw_value_decides_near_a_hard_limit(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            trace = folder/"noise.csv"
            trace.write_text("frequency_Hz,out_V2_per_Hz\n0,1.0000004\n1,1.0000004\n")
            spec = dict(method="band_noise_rms",file=trace.name,axis="frequency_Hz",density="out_V2_per_Hz",
                        density_unit="V2/Hz",band_Hz=[0,1])
            target = dict(source="regression",confirmed=True,limits={"<=":1})
            test = dict(unit="V RMS",recompute=spec,target=target)
            context = dict(context_sha256="test-context")
            result = dict(status="measured",unit="V RMS",value=1.0,context_sha256="test-context",
                          evidence={trace.name:hashlib.sha256(trace.read_bytes()).hexdigest()})
            review = checks.review_measurement(test,context,result,folder,{})
            self.assertEqual(review["status"],"FAIL")
            self.assertGreater(review["value"],1)

    def test_rejected_units_and_band(self):
        test = json.loads(bench.FIXTURES.read_bytes())[0]["test"]
        for field,value in (("method","rms"),("axis","frequency_kHz"),("density_unit","A/sqrt(Hz)"),
                            ("band_Hz",[100,0]),("band_Hz",[0,math.inf])):
            with self.subTest(field=field,value=value):
                spec = dict(test["recompute"])
                spec[field] = value
                with self.assertRaises(ValueError):
                    checks.validate_recompute(spec,test["unit"])

    def test_changed_measurement_band_invalidates_comparison(self):
        test = json.loads(bench.FIXTURES.read_bytes())[0]["test"]
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder/"source").write_text("frozen fixture")
            plan = dict(tests=[test],inputs=["source"],reference_inputs=["source"])
            initial = checks.snapshot(plan,folder,{})[test["id"]]
            variant = copy.deepcopy(plan)
            variant["tests"][0]["recompute"]["band_Hz"][0] += 1
            changed = checks.snapshot(variant,folder,{})[test["id"]]
            self.assertNotEqual(initial["reference_sha256"],changed["reference_sha256"])

    def test_inapplicable_diagnostic_cannot_waive_a_required_measurement(self):
        required = json.loads(bench.FIXTURES.read_bytes())[0]["test"]
        diagnostic = dict(required,id="optional",role="diagnostic",not_applicable_reason="The fixture has no CMFB loop")
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            source = folder/"dut"
            source.write_text("test circuit")
            p = folder/"plan.json"
            plan = dict(schema_version=1,inputs=["dut"],tests=[required,diagnostic],results={})
            p.write_text(json.dumps(plan))
            validated, contexts = checks.load_records(p,{})
            result = checks.check(validated,folder,contexts,{})
            self.assertEqual([r["disposition"] for r in result["tests"]],["NOT_RUN","NOT_APPLICABLE"])
            self.assertEqual(result["status"],"REVIEW_REQUIRED")
            required["not_applicable_reason"] = "missing data"
            p.write_text(json.dumps(plan))
            with self.assertRaises(ValueError):
                checks.load_plan(p)
