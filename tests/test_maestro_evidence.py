"""Regression checks for rejecting incomplete native histories and ineffective mismatch."""

import sys
import tempfile
import unittest
from pathlib import Path

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/dynamic-comparator"
sys.path.insert(0, str(EXAMPLE))
import maestro
import mismatch
import schematic


class NativeEvidenceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_history_requires_exact_configured_tests_and_one_design_point(self):
        folder = self.root / "maestro-evidence"
        folder.mkdir()
        (folder / "maestro-history.txt").write_text('"Interactive.0"\n""\n')
        base = self.root / "maestro-results" / maestro.LIB / maestro.TB / "maestro/results/maestro/Interactive.0"
        (base / "1/NOM_POS").mkdir(parents=True)
        expected = ("NOM_NEG", "NOM_POS")
        with self.assertRaisesRegex(ValueError, "Missing or unexpected"):
            maestro.history_tests(self.root, expected)
        (base / "1/NOM_NEG").mkdir()
        self.assertEqual(set(maestro.history_tests(self.root, expected)[1]), set(expected))
        (base / "2").mkdir()
        with self.assertRaisesRegex(ValueError, "one native Maestro design point"):
            maestro.history_tests(self.root, expected)

    def test_native_deck_binds_terminals_geometry_signed_input_load_corner_and_solver(self):
        case = dict(vdd_V=1.8, vcm_V=0.9, delta_mV=-5, cload_fF=20, ramp_ns=0.1, temp_C=27, corner="tt")
        control = "subckt C D G S B\nM1 (D G S B) nmos w=1u\nends C\nXDUT (D G S 0) C\n"
        settings = "parameters VDDVAL=1.8 VCM=0.9 VDIFF=-5m RSRC=1k CLVAL=20f TRSUP=0.1n\n"
        deck = (settings + 'include "example.lib" section=tt\ninclude "save.scs"\n' + control +
                "simulatorOptions options temp=27 reltol=1e-5 vabstol=1e-7 iabstol=1e-13\n"
                "tran tran stop=39n maxstep=5p strobeperiod=5p strobeoutput=strobeonly errpreset=conservative\n")
        maestro.check_deck(deck, control, case, "example.lib", "save.scs")
        for old, new in (("=20f", "=35f"), ("=-5m", "=5m"), ("=tt", "=ss"), ("(D G S B)", "(G D S B)"),
                         ("=1u", "=2u"), ("temp=27", "temp=85"), ("stop=39n", "stop=29n")):
            with self.subTest(change=new), self.assertRaises(ValueError):
                maestro.check_deck(deck.replace(old, new), control, case, "example.lib", "save.scs")

    def test_mismatch_retains_scope_and_requires_actual_draw_ids(self):
        path = self.root / "mcdata"
        path.write_text("#1\n0.1 0.3\n\n#2\n0.2 0.4\n")
        self.assertEqual(mismatch.sampled_vectors(path, 2), 2)
        for broken in ("#1\n0.1 0.3\n", "#1\n0.1\n#1\n0.2\n", "#0\n0.1\n#1\n0.2\n",
                       "#1\n0.1 0.3\n#2\nnan 0.4\n", "#1\n0.1 0.3\n#2\n0.2\n"):
            path.write_text(broken)
            with self.assertRaises(ValueError):
                mismatch.sampled_vectors(path, 2)

    def test_correct_decisions_and_random_numbers_do_not_prove_mismatch_sensitivity(self):
        rows = [{"internal_diff_V": [0.03, 0.3, 0.7]}, {"internal_diff_V": [0.03, 0.3 + 1e-13, 0.7]}]
        self.assertFalse(mismatch.sensitivity(rows, 2)["response_observed"])
        rows[1]["internal_diff_V"][1] += 0.001
        self.assertFalse(mismatch.sensitivity(rows, 1)["response_observed"])
        self.assertTrue(mismatch.sensitivity(rows, 2)["response_observed"])

    def test_statistical_mapping_preserves_all_drawn_terminals_and_sizes(self):
        for variant in ("baseline", "candidate"):
            _, nominal = schematic.core_schematic(variant, "examplePDK")
            _, statistical = schematic.core_schematic(variant, "examplePDK", "statistical")
            self.assertEqual(nominal.keys(), statistical.keys())
            for name, old in nominal.items():
                new = statistical[name]
                self.assertEqual(new["cell"], old["cell"] + "_ckt")
                self.assertEqual({k: v for k, v in new.items() if k != "cell"},
                                 {k: v for k, v in old.items() if k != "cell"})

    def test_registry_keeps_evaluation_sources_out_of_the_learning_material(self):
        import json
        root = EXAMPLE.parents[1]
        rows = [json.loads(line) for line in (root / "research/sources.jsonl").read_text(encoding="utf-8").splitlines()]
        catalog = {row["repo"]: row for row in rows}
        self.assertEqual(len(catalog), len(rows))
        for row in rows:
            self.assertRegex(row["commit"], r"^[0-9a-f]{40}$")
            if row["tier"] == "EVAL":
                self.assertNotIn("inspected", row)
                self.assertNotIn("finding", row)
        for name in ("Arcadia-1/razavi-bench", "Arcadia-1/analog-design-bench"):
            self.assertEqual(catalog[name]["tier"], "EVAL")

    def test_mc_deck_replaces_only_the_selected_section_and_encloses_the_original_analysis(self):
        source = 'include "pdk.lib" section=tt\nM1 (d g s b) nmos w=1u\ntran tran stop=39n\n'
        deck = mismatch.mc_deck(source, "pdk.lib", "tt", "mc", 16, 5317)
        self.assertIn('include "pdk.lib" section=mc\nM1 (d g s b) nmos w=1u\n', deck)
        self.assertIn("numruns=16 seed=5317 {\ntran tran stop=39n\n}", deck)
        with self.assertRaises(ValueError):
            mismatch.mc_deck(source, "other.lib", "tt", "mc", 16, 5317)
