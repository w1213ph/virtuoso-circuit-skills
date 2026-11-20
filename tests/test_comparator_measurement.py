"""Synthetic waveform tests for the native comparator example's measurement script."""

import runpy
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "examples/dynamic-comparator/measure_psf.py"
analyze = runpy.run_path(str(SCRIPT))["analyze"]


def fixture(sign):
    rows = ["VALUE"]
    for tick in range(780):
        phase = (tick - 160) % 200 if tick >= 160 else 100
        clock = 1.8 if 2 <= phase <= 82 else 0.9 if phase in (1, 83) else 0
        resolved = 10 <= phase <= 82
        points = {
            "time": tick * 50e-12, "CLK": clock, "VDD": 1.8, "VSUP:p": -1e-6,
            "INP": 0.9 + 0.0025 * sign, "INN": 0.9 - 0.0025 * sign,
            "OUTP": 0 if resolved and sign < 0 else 1.8, "OUTN": 0 if resolved and sign > 0 else 1.8,
        }
        rows.extend(f'"{name}" {value}' for name, value in points.items())
    return "\n".join([*rows, "END"])


class ComparatorMeasurement(unittest.TestCase):
    def test_two_polarities_and_wrong_polarity(self):
        for sign in (1, -1):
            with self.subTest(sign=sign):
                traces = fixture(sign)
                verified = analyze(traces, 1.8, 0.9, sign * 5)
                self.assertTrue(verified["precharge_and_decisions_pass"])
                self.assertEqual(verified["valid_decisions"], 3)
                self.assertAlmostEqual(verified["worst_delay_ps"], 450)
                self.assertAlmostEqual(verified["mean_core_energy_fJ"], 18)
                mismatch = analyze(traces, 1.8, 0.9, -sign * 5)
                self.assertEqual(mismatch["valid_decisions"], 0)
                self.assertIsNone(mismatch["worst_delay_ps"])

    def test_missing_trace_is_rejected(self):
        traces = fixture(1).replace('"VSUP:p"', '"unrelated_current"')
        with self.assertRaisesRegex(ValueError, "Missing scalar traces"):
            analyze(traces, 1.8, 0.9, 5)


if __name__ == "__main__":
    unittest.main()
