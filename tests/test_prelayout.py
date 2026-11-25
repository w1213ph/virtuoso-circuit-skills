"""Exercise the pre-layout command against small, explicitly synthetic fixtures."""

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "skills/paper-to-virtuoso/scripts/check_prelayout.py"


class PrelayoutCommands(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        self.comparator = {
            "id": "decision_delay",
            "metric": "Comparator decision delay",
            "unit": "ns",
            "testbench": "TB_COMP_DELAY",
            "analysis": "tran",
            "conditions": {"corner": "TT", "temperature_C": 25, "output_load_fF": 30},
            "measurement": "From the evaluation edge to the first stable correct decision",
            "target": {"source": "Fixture target", "confirmed": True, "limits": {"<=": 8}},
        }
        self.vco = {
            "id": "minimum_tuning_frequency",
            "metric": "Minimum frequency over the declared tuning sweep",
            "unit": "MHz",
            "testbench": "TB_VCO_SWEEP",
            "analysis": "tran",
            "conditions": {"control_V": [0.3, 0.4, 0.5], "corner": "SS"},
            "measurement": "Minimum steady-state frequency across all declared control values",
            "target": {"source": "Fixture target", "confirmed": True, "limits": {">": 100}},
        }

    def plan(self, name, test):
        directory = self.root / name
        directory.mkdir()
        (directory / "dut.txt").write_text(f"Synthetic DUT revision {name}\n", encoding="utf-8")
        (directory / "bench.txt").write_text("Fixed bench and model fixtures\n", encoding="utf-8")
        record = {
            "schema_version": 1,
            "inputs": ["dut.txt"],
            "reference_inputs": ["bench.txt"],
            "tests": [test],
            "results": {},
        }
        path = directory / "prelayout.json"
        self.save(path, record)
        return path, record

    def save(self, path, plan):
        path.write_text(json.dumps(plan), encoding="utf-8")

    def invoke(self, operation, path, expected=0, baseline=None):
        command = [sys.executable, str(SCRIPT), operation, "--plan", str(path)]
        if baseline is not None:
            command.extend(["--baseline", str(baseline)])
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, expected, result.stderr or result.stdout)
        return json.loads(result.stdout)

    def measure(self, path, plan, value, filename="measurement.txt"):
        test = plan["tests"][0]
        context = self.invoke("snapshot", path)["tests"][test["id"]]
        evidence = path.parent / filename
        evidence.write_text(f"Synthetic test measure: {value} {test['unit']}\n", encoding="utf-8")
        record = {
            "status": "measured",
            "context_sha256": context["context_sha256"],
            "value": value,
            "unit": test["unit"],
            "evidence": {evidence.name: hashlib.sha256(evidence.read_bytes()).hexdigest()},
        }
        plan["results"][test["id"]] = record
        self.save(path, plan)
        return record

    def test_empty_plan_and_missing_measurement_require_review(self):
        path, plan = self.plan("candidate", self.comparator)
        report = self.invoke("check", path, expected=1)
        self.assertEqual(report["tests"][0]["status"], "NOT_RUN")
        plan["tests"] = []
        self.save(path, plan)
        report = self.invoke("check", path, expected=1)
        self.assertEqual(report["status"], "REVIEW_REQUIRED")

    def test_comparator_improvement_from_failing_baseline(self):
        baseline, baseline_plan = self.plan("baseline", self.comparator)
        candidate, candidate_plan = self.plan("candidate", self.comparator)
        self.measure(baseline, baseline_plan, 8.5)
        self.measure(candidate, candidate_plan, 7)
        comparison = self.invoke("compare", candidate, baseline=baseline)
        self.assertTrue(comparison["candidate_limits_met"])
        self.assertEqual(comparison["passing_variants"], ["candidate"])
        self.assertEqual(comparison["tests"][0]["baseline_status"], "FAIL")
        self.assertEqual(comparison["tests"][0]["delta"], -1.5)

    def test_vco_checks_strict_limit_and_reports_failing_candidate(self):
        baseline, baseline_plan = self.plan("baseline", self.vco)
        candidate, candidate_plan = self.plan("candidate", self.vco)
        self.measure(baseline, baseline_plan, 110)
        self.measure(candidate, candidate_plan, 100)
        report = self.invoke("check", candidate, expected=1)
        self.assertEqual(report["tests"][0]["status"], "FAIL")
        comparison = self.invoke("compare", candidate, baseline=baseline)
        self.assertEqual(comparison["status"], "COMPARABLE")
        self.assertFalse(comparison["candidate_limits_met"])

    def test_changed_condition_or_dut_invalidates_result(self):
        path, plan = self.plan("candidate", self.vco)
        self.measure(path, plan, 115)
        self.assertEqual(self.invoke("check", path)["status"], "RECORDED_LIMITS_MET")
        (path.parent / "dut.txt").write_text("Changed DUT\n", encoding="utf-8")
        self.assertEqual(self.invoke("check", path, expected=1)["tests"][0]["status"], "STALE_RESULT")
        self.measure(path, plan, 117)
        plan["tests"][0]["conditions"]["corner"] = "FF"
        self.save(path, plan)
        self.assertEqual(self.invoke("check", path, expected=1)["tests"][0]["status"], "STALE_RESULT")

    def test_individually_passing_candidates_with_different_models_are_incomparable(self):
        baseline, baseline_plan = self.plan("baseline", self.comparator)
        candidate, candidate_plan = self.plan("candidate", self.comparator)
        (candidate.parent / "bench.txt").write_text("Different model fixture\n", encoding="utf-8")
        self.measure(baseline, baseline_plan, 7)
        self.measure(candidate, candidate_plan, 6)
        self.assertEqual(self.invoke("check", candidate)["status"], "RECORDED_LIMITS_MET")
        comparison = self.invoke("compare", candidate, expected=1, baseline=baseline)
        self.assertEqual(comparison["tests"][0]["status"], "REFERENCE_MISMATCH")
        self.assertFalse(comparison["candidate_limits_met"])
        self.assertEqual(comparison["passing_variants"], [])

    def test_missing_and_changed_result_evidence_are_rejected(self):
        path, plan = self.plan("candidate", self.comparator)
        self.measure(path, plan, 7)
        evidence = path.parent / "measurement.txt"
        evidence.write_text("A different result\n", encoding="utf-8")
        self.assertEqual(self.invoke("check", path, expected=1)["tests"][0]["status"], "CHANGED_EVIDENCE")
        evidence.unlink()
        self.assertEqual(self.invoke("check", path, expected=1)["tests"][0]["status"], "MISSING_EVIDENCE")

    def test_bad_unit_and_unconfirmed_target_require_review(self):
        self.comparator["target"]["confirmed"] = False
        path, plan = self.plan("candidate", self.comparator)
        result = self.measure(path, plan, 7)
        self.assertEqual(self.invoke("check", path, expected=1)["tests"][0]["status"], "TARGET_UNCONFIRMED")
        result["unit"] = "ps"
        self.save(path, plan)
        self.assertEqual(self.invoke("check", path, expected=1)["tests"][0]["status"], "INVALID_MEASUREMENT")

    def test_unavailable_inputs_and_failed_run_are_reported(self):
        path, plan = self.plan("candidate", self.comparator)
        result = self.measure(path, plan, 7)
        result.update(status="run_failed", reason="Fixture convergence failure")
        del result["value"]
        self.save(path, plan)
        self.assertEqual(self.invoke("check", path, expected=1)["tests"][0]["status"], "RUN_FAILED")
        (path.parent / "dut.txt").unlink()
        self.assertEqual(self.invoke("check", path, expected=1)["tests"][0]["status"], "INPUT_UNAVAILABLE")

    def test_diagnostic_limits_do_not_qualify_a_design_or_replace_a_required_test(self):
        diagnostic = dict(self.comparator, role="diagnostic")
        path, plan = self.plan("diagnosis", diagnostic)
        self.measure(path, plan, 4)
        row = self.invoke("check", path, expected=1)["tests"][0]
        self.assertEqual((row["role"], row["status"], row["within_limits"]), ("diagnostic","DIAGNOSTIC_MEASURED",True))
        self.assertEqual(self.invoke("compare",path,baseline=path,expected=1)["passing_variants"], [])
        actual, actual_plan = self.plan("actual", self.comparator)
        self.measure(actual, actual_plan, 9)
        self.assertEqual(self.invoke("compare",path,baseline=actual,expected=1)["status"], "INCOMPARABLE")
        # Relabeling a saved diagnostic changes its context.
        del plan["tests"][0]["role"]
        self.save(path,plan)
        self.assertEqual(self.invoke("check",path,expected=1)["tests"][0]["status"], "STALE_RESULT")

    def test_optional_diagnostic_keeps_its_evidence_status_without_changing_selection(self):
        base, p = self.plan("base", self.comparator)
        self.measure(base,p,7)
        trial, q = self.plan("trial", dict(self.comparator, role="acceptance"))
        self.measure(trial,q,6)
        probe = dict(self.comparator, id="internal_node", role="diagnostic", target=None)
        q["tests"].insert(0,probe)
        self.save(trial,q)
        report = self.invoke("check",trial)
        self.assertEqual(report["tests"][0]["status"], "NOT_RUN")
        self.measure(trial,q,19,filename="diagnostic.txt")
        self.assertEqual(self.invoke("check",trial)["tests"][0]["status"], "DIAGNOSTIC_MEASURED")
        cmp = self.invoke("compare",trial,baseline=base)
        self.assertEqual(cmp["passing_variants"], ["baseline","candidate"])
        self.assertEqual([r["id"] for r in cmp["tests"]], [self.comparator["id"]])
        (trial.parent/"diagnostic.txt").write_bytes(b"changed")
        diag = self.invoke("check",trial)["tests"][0]
        self.assertEqual((diag["role"],diag["status"]), ("diagnostic","CHANGED_EVIDENCE"))


if __name__ == "__main__":
    unittest.main()
