"""Signed threshold and symmetric native sizing regression tests."""

from pathlib import Path
import sys
import tempfile
import unittest

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/dynamic-comparator"
sys.path.insert(0, str(EXAMPLE))
from offset import bracket, classify
from replay_mismatch import make_viewer
from schematic import core_schematic, width_overrides


class OffsetTests(unittest.TestCase):
    def test_viewer_indexes_only_the_selected_draw_and_preserves_its_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "psf"
            raw.mkdir()
            (raw / "logFile").write_text(
                'HEADER\nTYPE\nVALUE\n"mc-006_sampletran-tran" "analysisInst" (\n"tran"\n'
                '"mc-006_sampletran.tran.tran"\n)\n"tran-tran" "analysisInst" (\n"tran"\n'
                '"tran.tran.tran"\n)\n"finalTimeOP-info" "analysisInst" (\n"info"\n)\nEND\n')
            (raw / "tran.tran.tran").write_bytes(b"nominal")
            selected = raw / "mc-006_sampletran.tran.tran"
            selected.write_bytes(b"draw-six")
            make_viewer(root, selected, 6, {"delta_mV": 5})
            self.assertEqual((root / "selected-psf/tran.tran.tran").read_bytes(), b"draw-six")
            index = (root / "selected-psf/logFile").read_text()
            self.assertEqual(index.count('"analysisInst" ('), 1)
            self.assertIn('"tran-tran"', index)
            self.assertNotIn("finalTimeOP", index)

    def test_brackets_keep_censored_unresolved_and_nonmonotone_draws(self):
        grid = [-5, 0, 5]
        for signs, state in (((-1, -1, 1), "bracketed"), ((1, 1, 1), "below_grid"),
                             ((-1, -1, -1), "above_grid"), ((-1, 0, 1), "unresolved"),
                             ((1, -1, 1), "nonmonotone")):
            with self.subTest(signs=signs):
                self.assertEqual(bracket(grid, signs)["status"], state)
        result = bracket(grid, [-1, -1, 1])
        self.assertEqual((result["last_negative_mV"], result["first_positive_mV"]), (0, 5))

    def test_zero_input_can_resolve_negative_and_a_disagreeing_cycle_remains_unresolved(self):
        ticks = range(7801)
        high = [i >= 200 and (i - 200) % 2000 < 800 for i in ticks]
        resolved = [i >= 200 and 150 <= (i - 200) % 2000 < 800 for i in ticks]
        data = {"time": [i * 5e-12 for i in ticks], "VDD": [1.8] * len(ticks),
                "CLK": [1.8 if v else 0 for v in high],
                "OUTP": [0 if v else 1.8 for v in resolved], "OUTN": [1.8] * len(ticks),
                "SRC_P": [.9] * len(ticks), "SRC_N": [.9] * len(ticks),
                "XDUT.XP": [0] * len(ticks), "XDUT.XN": [0] * len(ticks)}
        case = {"vdd_V": 1.8, "delta_mV": 0, "vcm_V": .9}
        result = classify(data, case)
        self.assertEqual(result["cycle_decisions"], [-1, -1, -1])
        self.assertGreater(result["delay_ps"], 500)
        for i in range(2350, 3000):
            data["OUTP"][i], data["OUTN"][i] = 1.8, 0
        mixed = classify(data, case)
        self.assertEqual(mixed["cycle_decisions"], [-1, 1, -1])
        self.assertIsNone(mixed["delay_ps"])
        data["SRC_P"][2189] = 1.0
        with self.assertRaisesRegex(ValueError, "stimulus"):
            classify(data, case)

    def test_override_scales_total_width_and_preserves_baseline_and_terminals(self):
        _, base = core_schematic("baseline", "test", "statistical")
        _, unchanged = core_schematic("baseline", "test", "statistical", {"input_pair": 4})
        self.assertEqual(unchanged, base)
        _, selected = core_schematic("candidate", "test", "statistical", {"input_pair": 4, "tail": 5.4})
        for name in ("MNINP", "MNINN"):
            self.assertEqual(selected[name]["parameters"]["fw"], "2u")
            self.assertEqual(selected[name]["terms"], base[name]["terms"])
        self.assertEqual(selected["MTAIL"]["parameters"]["fw"], "2.7u")
        for bad in ({"MNINP": 4}, {"input_pair": False}, {"tail": -2}, {"latch_p": float("nan")}):
            with self.assertRaises(ValueError):
                width_overrides(bad)
