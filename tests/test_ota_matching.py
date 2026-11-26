"""Keep pair-area interventions, nominal selection and sampled diagnoses independently checkable."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

CASE = Path(__file__).resolve().parents[1]/"examples/smic18-ota-sizing"
sys.path.insert(0,str(CASE))
from ota_matching import PAIR,PROBES,comparison,main,scope_check
from ota_match_qualify import HOLDOUT,choose,constant_offset_residual,input_area_estimate,validation_seed
from ota_views import CMFB,INPUT,sources
from paired_outcomes import compare_passes


class PhysicalExperiment(unittest.TestCase):
    def test_generator_only_changes_the_named_matched_devices(self):
        sizes = dict(length_um=1.5,input=1.3,tail=2.75,load=5.05,driver=14.75,sink=4.1,reference=1.35,
                     bias_ohm=1075000,null_ohm=18905,plate_um=27.5,cm_input=.45,cm_length_um=3.75)
        normal,baseline = sources("example",sizes)
        self.assertEqual(normal,sources("example",dict(sizes,paired_geometry={}))[0])
        for name,geometry in PROBES:
            with self.subTest(variant=name):
                expected = copy.deepcopy(baseline)
                for role,cell,names in (("input_pair",INPUT,("MINP","MINN")),("cm_tails",CMFB,("MCTP","MCTN")),
                                        ("cm_loads",CMFB,("MCM","MDMY"))):
                    if role in geometry:
                        w,l = geometry[role]
                        for mos in names:
                            expected[cell][mos]["parameters"].update(fw="{:g}u".format(w),l="{:g}u".format(l))
                self.assertEqual(sources("example",dict(sizes,paired_geometry=geometry))[1],expected)

    def test_native_comparison_rejects_any_additional_electrical_change(self):
        ordinary = ("subckt FDOTA18_INPUT a b c d\nMINP (a b c d) n18 w=1.3u l=1.5u nf=1\n"
                    "MINN (e f c d) n18 w=1.3u l=1.5u nf=1\nMTAIL (c g 0 0) n18 w=2.75u l=1.5u\n"
                    "ends FDOTA18_INPUT\nCLP (out 0) capacitor c=2p\n")
        changed = ordinary.replace("w=1.3u l=1.5u","w=1.95u l=2.25u")
        scope_check(ordinary,changed,PAIR)
        for wrong in (changed.replace("1.95u","1.94u",1),changed.replace("2.75u","1.95u"),changed.replace("a b c d)","c b a d)"),
                      changed.replace("nf=1","nf=2"),changed.replace("2p","1p")):
            with self.assertRaises(ValueError):
                scope_check(ordinary,wrong,PAIR)

    def test_candidate_is_frozen_only_after_static_and_both_dynamic_edges_pass(self):
        probes = {"pair":{"static_passed":18,"dynamic_passed":5},"load":{"static_passed":17,"dynamic_passed":6}}
        self.assertIsNone(choose(probes,["pair","load"]))
        probes["load"]["static_passed"] = 18
        self.assertEqual(choose(probes,["pair","load"]),"load")


class SampleDiagnostics(unittest.TestCase):
    def test_reserved_seed_remains_frozen_on_replay(self):
        self.assertEqual(validation_seed(None),HOLDOUT)
        self.assertEqual(validation_seed(None,314159),314159)
        self.assertEqual(validation_seed(314159,314159),314159)
        for value in (0,64211,97337):
            with self.assertRaises(ValueError):
                validation_seed(value)
        with self.assertRaises(ValueError):
            validation_seed(314159,HOLDOUT)

    def test_separate_screen_and_qualification_keep_the_original_reservation(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)/"study"
            argv = ["ota_matching.py","screen","--config",folder,"--ota",folder,
                    "--cmfb",folder,"--robust",folder,"--out",str(out),"--validation-seed","314159"]
            with patch.object(sys,"argv",argv), patch("ota_matching.config_for_site",return_value={"model_file":"model"}), \
                    patch("ota_matching.sha256",return_value="fixture"), patch("ota_matching.study",return_value={}):
                self.assertEqual(main(),0)
            self.assertEqual(json.loads((out/"validation-seed.json").read_text())["validation_seed"],314159)
            argv[1],argv[-1] = "qualify","314160"
            with patch.object(sys,"argv",argv), self.assertRaisesRegex(ValueError,"frozen run"):
                main()

    def test_shared_comparison_tracks_each_verified_draw_independent_of_row_order(self):
        old = [("a",True),("b",False),("c",True),("d",False)]
        changed = [("c",False),("d",False),("a",True),("b",True)]
        self.assertEqual(compare_passes(old,changed),dict(kept_pass=1,gained_pass=1,lost_pass=1,both_failed=1))
        for wrong in (changed[:-1],changed+[("b",False)],changed+[("",True)],[("a",1)]+changed[1:]):
            with self.assertRaises(ValueError):
                compare_passes(old,wrong)
        with self.assertRaises(ValueError):
            compare_passes([],[])

    @staticmethod
    def cases(scale=1.0):
        def run(v):
            return dict(ids=["physical-a","physical-b"],descriptor_sha256="fields",
                        samples=[[dict(command_V=x,signed_diff_error_mV=(offset+v)*scale,failed=["tracking"])
                                  for x in (-.3,0,.3)] for offset in (-3,2)])
        return {"tt-950":run(0),"ff-950":run(.4)}

    def test_pair_comparison_uses_the_same_draw_across_geometries(self):
        a,b = self.cases(),self.cases(.5)
        outcome = comparison(a,b)
        self.assertEqual(outcome["improved_draws"],2)
        self.assertEqual(outcome["after"]["center_sample_sigma_mV"],outcome["before"]["center_sample_sigma_mV"]/2)
        b["ff-950"]["ids"].reverse()
        with self.assertRaises(ValueError):
            comparison(a,b)

    def test_static_offset_diagnostic_keeps_hard_failures_and_signed_errors(self):
        cases = self.cases()
        out = constant_offset_residual(cases)
        self.assertEqual(out["within_1mV_after_arithmetic_subtraction"],2)
        self.assertAlmostEqual(out["worst_residual_mV"],.4)
        cases["ff-950"]["samples"][0][0]["signed_diff_error_mV"] = 3
        self.assertEqual(constant_offset_residual(cases)["within_1mV_after_arithmetic_subtraction"],1)
        self.assertEqual(cases["tt-950"]["samples"][0][1]["failed"],["tracking"])

    def test_area_projection_charges_both_devices_and_rejects_a_changed_w_over_l(self):
        measured = comparison(self.cases(),self.cases(2/3))
        predicted = input_area_estimate((1.3,1.5),(1.95,2.25),107.7,measured,1)
        self.assertAlmostEqual(predicted["maximum_pair_um2_at_fixed_others"],10.39)
        self.assertAlmostEqual(predicted["predicted_sigma_ratio"],2/3)
        self.assertAlmostEqual(predicted["measured_sigma_ratio"],2/3)
        for geometry in ((1.95,1.5),(3.9,4.5),(0,0)):
            with self.assertRaises(ValueError):
                input_area_estimate((1.3,1.5),geometry,107.7,measured,1)
