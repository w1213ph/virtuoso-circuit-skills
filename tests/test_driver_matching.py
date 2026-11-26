import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CASE = Path(__file__).resolve().parents[1] / "examples/sky130-adc-driver"
sys.path[:0] = [str(CASE), str(CASE.parent / "sky130-current-mirror")]
from driver import OTA, materialize
from matching_design import check_sizing, family, pick


def score(area, error, passed=32, pvt=True):
    return dict(area_um2=area, worst_error_mV=error, passed=passed, samples=32, pvt_pass=pvt)


class DriverMatchingTests(unittest.TestCase):
    def test_cross_version_sums_preserve_exact_native_geometry(self):
        saved = dict(core_gate_area_um2=398.4, switch_gate_area_um2=1.35, devices={"XIP": [6, 2, 9]})
        check_sizing(saved, dict(saved, core_gate_area_um2=398.40000000000003))
        with self.assertRaisesRegex(ValueError, "devices"):
            check_sizing(saved, dict(saved, devices={"XIP": [6.00000000001, 2, 9]}))
        with self.assertRaisesRegex(ValueError, "core_gate_area"):
            check_sizing(saved, dict(saved, core_gate_area_um2=398.39))

    def test_reserve_is_a_preference_after_hard_limits(self):
        choices = {
            "small": score(10, 3.8),
            "middle": score(14, 3.45),
            "larger": score(19, 3.1),
            "failed_phase_margin": score(8, 3.0, pvt=False),
            "one_failure": score(7, 4.1, 31),
        }
        self.assertEqual(pick(choices, 4, 10), ("middle", "reserve_met"))
        self.assertEqual(pick(choices, 4, 0), ("small", "reserve_met"))
        self.assertEqual(pick(choices, 4, 40), ("small", "sample_limits_met"))

    def test_no_winner_remains_diagnostic(self):
        choices = {
            "nominal_failed": score(10, 4.1, 31, False),
            "fewer_passes": score(20, 5, 17),
            "most_passes": score(30, 4.2, 29),
        }
        self.assertEqual(pick(choices, 4, 10), ("most_passes", "diagnostic_only"))

    def test_training_and_validation_cannot_share_draws(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "new-run"
            p = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(CASE / "matching.py"),
                    "run",
                    "--out",
                    str(out),
                    "--samples",
                    "4",
                    "--training-seed",
                    "23",
                    "--validation-seed",
                    "25",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(p.returncode, 2)
            self.assertIn("must be disjoint", p.stderr)
            self.assertFalse(out.exists())

    def test_physical_ratio_is_visible_and_counted_in_native_area(self):
        spec = json.loads((CASE / "requirements.example.json").read_bytes())
        rows = [
            dict(polarity=p, length_um=l, vds_V=v, vbs_V=b, gm_id_per_V=g, abs_id_uA=3, id_per_width_uA_um=d)
            for p, l, v, b, g, d in (
                ("N", 1, 0.65, -0.15, 16, 1.15),
                ("N", 2, 0.65, -0.15, 16, 0.6),
                ("P", 1, -0.65, 0, 15, 0.287),
                ("P", 1.5, -0.65, 0, 15, 0.225),
                ("P", 2, -0.65, 0, 15, 0.2),
            )
        ]
        sizes = family(rows, spec)
        ratio = sizes["ratio"]["devices"]
        self.assertEqual(ratio["XLA"], (2.5, 1, 24))
        self.assertEqual(ratio["XLB"], (2.5, 1, 25))
        repair = family(rows, spec, repair=True)
        self.assertEqual(repair["fine-ratio"]["devices"]["XLA"], (1.6, 1.5, 50))
        self.assertEqual(repair["fine-ratio"]["devices"]["XLB"], (1.6, 1.5, 52))
        for row in list(sizes.values()) + list(repair.values()):
            self.assertAlmostEqual(
                row["core_gate_area_um2"],
                sum(w * l * m for k, (w, l, m) in row["devices"].items() if k in OTA),
            )
            for group in ("XPREF", "XNREF", "XTAIL", "XSN", "XSP"):
                self.assertEqual(row["devices"][group], sizes["original"]["devices"][group])
        with tempfile.TemporaryDirectory() as tmp:
            materialize(Path(tmp), sizes["ratio"], spec)
            schematic = (Path(tmp) / "ADRV_OTA.sch").read_text(encoding="utf-8")
            self.assertIn("24 at VX and 25 at OUT", schematic)

    def test_redesign_confirmation_has_no_previous_samples(self):
        code = "import json; from matching import seed_blocks; print(json.dumps(seed_blocks(24701,54701,84701,32)))"
        blocks = json.loads(subprocess.check_output([sys.executable, "-B", "-c", code], cwd=CASE))
        self.assertEqual([len(block) for block in blocks], [32, 32, 64])
        self.assertEqual(len(set(sum(blocks, []))), 128)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "new-run"
            for recycled in (24732, 54640):
                p = subprocess.run(
                    [
                        sys.executable,
                        "-B",
                        str(CASE / "matching.py"),
                        "run",
                        "--out",
                        str(out),
                        "--confirmation-seed",
                        str(recycled),
                    ],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(p.returncode, 2)
                self.assertIn("must be disjoint", p.stderr)
                self.assertFalse(out.exists())
