import sys
import sqlite3
import tempfile
import unittest
import xml.etree.ElementTree as ET
from contextlib import closing
from pathlib import Path

CASE = Path(__file__).resolve().parents[1] / "examples/smic18-bias-mirror"
sys.path.insert(0, str(CASE))
import bias
from bias_ade import check_plotted_outputs, matches_options, options
from bias_views import LIB, TB
from measure import sample_metrics


def geometry(area, target, state="eligible"):
    return {
        "state": state,
        "sizing": {"mirror_pair_gate_area_um2": area, "length_um": 0.5, "target_gm_id_per_V": target},
    }


class NativeBiasTests(unittest.TestCase):
    def test_remeasure_after_both_initial_designs_fail(self):
        grid = {
            "small": geometry(1, 12),
            "initial1": geometry(3, 15),
            "initial2": geometry(11, 15),
            "passing": geometry(6, 18),
            "large": geometry(8, 20),
            "invalid": geometry(0, 12, "outside_width_range"),
        }
        order = []

        def measure(name, sizing):
            order.append(name)
            return {"passed": name == "passing"}

        selected, _ = bias.search(grid, measure)
        self.assertEqual(order, ["initial1", "initial2", "small", "passing"])
        self.assertEqual(selected, "passing")
        self.assertEqual(grid["large"]["state"], "area_bound")
        self.assertEqual(grid["invalid"]["state"], "outside_width_range")

    def test_all_electrical_failures_are_kept(self):
        grid = {
            "seed": geometry(3, 15),
            "next": geometry(6, 18),
            "duplicate": geometry(6, 20, "same_geometry"),
        }
        selected, measured = bias.search(grid, lambda name, sizing: {"passed": False})
        self.assertIsNone(selected)
        self.assertEqual(set(measured), {"seed", "next"})
        self.assertEqual(grid["next"]["state"], "measured")

    def test_passing_seed_still_compares_smaller_candidate(self):
        grid = {"seed": geometry(3, 15), "small": geometry(1, 12), "large": geometry(5, 18)}
        selected, measured = bias.search(grid, lambda name, sizing: {"passed": True})
        self.assertEqual(selected, "small")
        self.assertEqual(set(measured), {"seed", "small"})

    def test_declared_dc_variable_with_missing_sweep_bounds_is_rejected(self):
        required = {"param": "VOUT", "start": "0", "stop": "1.5", "step": "0.01"}
        generated = "dc dc param=VOUT step=10m"
        with self.assertRaisesRegex(ValueError, "ADE option start differs"):
            matches_options(options(generated, "dc dc"), required)
        matches_options(options(generated + " start=0 stop=1.5", "dc dc"), required)

    def test_saved_output_must_have_an_evaluated_wave_in_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp)
            view = stage / LIB / TB / "maestro"
            (view / "test_states").mkdir(parents=True)
            (view / "results/maestro").mkdir(parents=True)
            expression = '(- IT("/VFORCE/PLUS"))'
            state = ET.Element("statedb")
            test = ET.SubElement(state, "Test", Name="TT_TRAN")
            for label, field, value in (
                ("IOUT_SINK", "expression", expression),
                ("VREF_NODE", "signal", '"/IREF"'),
            ):
                row = ET.SubElement(test, "field", Type="defstruct")
                ET.SubElement(row, "field", Name="name").text = '"' + label + '"'
                ET.SubElement(row, "field", Name=field).text = value
            for path in ("active.state", "test_states/Interactive.0.state"):
                ET.ElementTree(state).write(str(view / path))
            with closing(sqlite3.connect(str(view / "results/maestro/Interactive.0.rdb"))) as db:
                db.executescript("""CREATE TABLE test(testID, name);
                    CREATE TABLE result(resultID, testID, name, expression);
                    CREATE TABLE resultValue(resultID, value, errorID);
                    INSERT INTO test VALUES(1,'TT_TRAN');
                    INSERT INTO resultValue VALUES(1,'wave',NULL);""")
                db.execute("INSERT INTO result VALUES(1,1,'IOUT_SINK',?)", (expression,))
                db.commit()
                check_plotted_outputs(stage, "Interactive.0", ["TT_TRAN"])
                db.execute("UPDATE resultValue SET value = '', errorID = 1")
                db.commit()
                with self.assertRaisesRegex(ValueError, "not evaluated as a waveform"):
                    check_plotted_outputs(stage, "Interactive.0", ["TT_TRAN"])

    def test_completed_waveform_that_never_settles_returns_observed_lower_bound(self):
        ref = 8e-6
        dc = [(i / 100, i / 100, 0.6, -ref * (1 + 0.01 * (i / 100 - 0.8)), -ref, 1.8) for i in range(151)]
        tran = [(i * 0.2e-9, 0.8, 0.6, 0, -ref * min(1, max(0, i * 0.2 - 10)), 1.8) for i in range(1501)]
        result = sample_metrics(dc, tran, 1.8, 8, 8, 32, allow_unsettled=True)
        self.assertIsNone(result["settling_ns"])
        self.assertAlmostEqual(result["settling_lower_bound_ns"], 289.01, places=3)
        with self.assertRaisesRegex(ValueError, "did not stay within"):
            sample_metrics(dc, tran, 1.8, 8, 8, 32)
        with self.assertRaisesRegex(ValueError, "Incomplete transient"):
            sample_metrics(dc, tran[:-5], 1.8, 8, 8, 32, allow_unsettled=True)
