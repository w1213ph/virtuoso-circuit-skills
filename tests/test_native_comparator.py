import csv
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


EXAMPLE = Path(__file__).resolve().parents[1] / "examples/dynamic-comparator"
sys.path.insert(0, str(EXAMPLE))
import audit_native
import characterize
import run_native
from spectre_run import read_json, save_json, sha256


class NativeComparatorTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.config = read_json(EXAMPLE / "native.example.json")

    def test_scenarios_create_both_polarities_and_require_unique_names(self):
        cases = run_native.cases(self.config)
        self.assertEqual(len(cases), 8)
        for pos, neg in zip(cases[::2], cases[1::2]):
            self.assertEqual(pos["delta_mV"], -neg["delta_mV"])
            self.assertEqual(pos["cload_fF"], neg["cload_fF"])
        self.config["scenarios"].append(self.config["scenarios"][0])
        with self.assertRaisesRegex(ValueError, "unique"):
            run_native.cases(self.config)

    def test_parent_leads_use_the_pin_net_and_outward_first_segment(self):
        terms, shapes = [], []
        for i, (pin, (dx, dy)) in enumerate(audit_native.OUTWARD.items()):
            anchor = [i * 3, 2]
            terms.append(dict(name=pin, net=pin, xy=anchor))
            shapes.append(dict(net=pin, points=[[anchor[0] + dx, anchor[1] + dy], anchor]))
        inst = {"terms": terms}
        self.assertEqual(set(audit_native.outward_leads(shapes, inst)), set(audit_native.OUTWARD))
        for wrong in ({"net": "wrong", "points": shapes[0]["points"]},
                      {"net": "INP", "points": [[0, 2], [1, 2]]},
                      {"net": "INP", "points": [[0, 2], [0, 1]]}):
            with self.subTest(wrong=wrong), self.assertRaisesRegex(ValueError, "INP"):
                audit_native.outward_leads([wrong] + shapes[1:], inst)

    def test_table_reuse_checks_recipe_nested_models_and_artifact_content(self):
        files = {}
        for name in characterize.FILES:
            path = self.root / name
            path.parent.mkdir(exist_ok=True)
            path.write_text(name)
            files[name] = sha256(path)
        model, nested = (self.root / name for name in ("model.lib", "section.lib"))
        for path in (model, nested):
            path.write_text("model_A")
        identity = {"deck_sha256": files["input.scs"], "spectre_version": "test"}
        marker = dict(identity=identity, files=files, dependencies={str(p): sha256(p) for p in (model, nested)},
                      sweeps=9, points=1629)
        save_json(self.root / "manifest.json", marker)
        self.assertEqual(characterize.reusable(self.root, identity, model), marker)
        self.assertIsNone(characterize.reusable(self.root, dict(identity, spectre_version="changed"), model))
        nested.write_text("model_B")
        self.assertIsNone(characterize.reusable(self.root, identity, model))
        nested.write_text("model_A")
        (self.root / "table.csv").write_text("changed")
        self.assertIsNone(characterize.reusable(self.root, identity, model))

    def test_phase_lookup_brackets_body_bias_and_declines_outside_data(self):
        table = self.root / "table.csv"
        fields = "polarity abs_vgs_V vds_V vbs_V total_width_um gm_over_id_per_V id_density_uA_per_um gm_uS".split()
        with table.open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(fields)
            for vbs in (0, -0.15):
                for vgs in (0.8, 0.85):
                    writer.writerow(["N", vgs, 0.5, vbs, 1.6, 4.9, 80, 670])
        point = dict(abs_vgs_V=0.84, vds_V=0.5, vbs_V=-0.08)
        rows = characterize.phase_lookup(table, point)
        self.assertEqual([r["vbs_V"] for r in rows], [-0.15, 0])
        self.assertTrue(all(r["abs_vgs_V"] == 0.85 for r in rows))
        self.assertEqual(characterize.phase_lookup(table, dict(point, vds_V=0.7)), [])

    def test_solver_failure_retains_snapshot_and_marks_all_its_metrics_failed(self):
        stage = self.root / "candidate"
        for name in ("evidence", "netlists", "simulation"):
            (stage / name).mkdir(parents=True)
        common = self.root / "common"
        common.mkdir()
        for name in ("testbench.scs", "measure_psf.py", "transient.txt", "model-dependencies.json", "tool.json"):
            (common / name).write_text("test")
        (stage / "netlists/netlist").write_text("test")
        save_json(stage / "acceptance.json", {})
        case = run_native.cases(self.config)[0]
        with patch.object(run_native, "run_spectre", side_effect=RuntimeError("solver stopped")), redirect_stdout(io.StringIO()):
            run_native.simulate_variant(stage, self.config, [case], "test", {})
        before = read_json(stage / "evidence/before-simulation.json")
        plan = read_json(stage / "prelayout.json")
        for key, result in plan["results"].items():
            self.assertEqual(result["context_sha256"], before[key]["context_sha256"])
            self.assertEqual(result["reason"], "solver stopped")
        checked = run_native.check_variant(stage)
        self.assertEqual([t["status"] for t in checked["tests"]], ["RUN_FAILED"] * 3)
        self.assertEqual(read_json(stage / "acceptance.json")["behavioral_simulation"]["status"], "review_required")
