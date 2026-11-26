"""Reject wrong code polarity, picked-again codes, partial sweeps and incomplete physical inventories."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"examples/smic18-ota-sizing"))
from ota_trim import BANKS, GEOMETRY, applied, code_summary
from ota_trim_decision import decision
from ota_trim_stats import score
from ota_trim_views import CODE_PINS, EXTRA_MOS, bit_gain
from ota_measure import OP_DEVICES
from ota_mismatch import CM_GROUPS, mask_mismatch
from ota_noise import group_for
from ota_robust import within_fullscale_deadline
from ota_views import BIAS, CORE, INPUT, INTERFACE, OUTPUT, PROBED, TB, interfaces, sources
from trim_calibration import held_measurements, select_codes


class Calibration(unittest.TestCase):
    def test_decision_rejects_mixed_runs_and_missing_conditions(self):
        nominal = {"fixed_code":7}
        same = {"nominal_sha256":"one","fixed_code":7}
        with self.assertRaisesRegex(ValueError,"same coded DUT"):
            decision(nominal,same,dict(same,fixed_code=8),"one")
        with self.assertRaisesRegex(ValueError,"same coded DUT"):
            decision(nominal,same,dict(same,nominal_sha256="two"),"one")
        with self.assertRaisesRegex(ValueError,"Incomplete"):
            decision(dict(nominal,fullscale={}),dict(same,grid={},operating={}),same,"one")

    def test_nominal_deadline_keeps_the_strobe_on_the_boundary(self):
        self.assertTrue(within_fullscale_deadline(1.00000000000022))
        self.assertFalse(within_fullscale_deadline(1.002))
        self.assertFalse(within_fullscale_deadline(None))

    def test_one_word_follows_the_physical_die(self):
        errors = {0:{"a":-.4,"b":2.1},1:{"b":-.1,"a":.4}}
        frozen = select_codes(errors,1)
        self.assertEqual(frozen,{"a":0,"b":1})
        changed = {1:{"b":.9,"a":.01},0:{"a":1.8,"b":.03}}
        self.assertEqual(held_measurements(frozen,changed),{"a":1.8,"b":.9})
        with self.assertRaises(ValueError):
            held_measurements(frozen,{0:{"a":.1,"b":.2},1:{"b":.5,"other":.2}})
        for bad in ({0:{"a":1.}}, {0:{"a":0},1:{"b":1}}, {0:{"a":float("nan")},1:{"a":0}}, {False:{"a":0},1:{"a":0}}):
            with self.assertRaises(ValueError):
                select_codes(bad,1)

    def test_calibration_gap_is_sorted_but_nonmonotonic_words_stay_visible(self):
        levels = [-7.5+i for i in range(16)]
        levels[8],levels[9] = levels[9],levels[8]
        curve = {str(i):{"points":[{},{"signed_error_mV":v},{}]} for i,v in enumerate(levels)}
        result = code_summary(curve)
        self.assertFalse(result["monotonic"])
        self.assertEqual((result["max_gap_mV"],result["nearest_zero_code"]),(1,7))
        del curve["9"]
        with self.assertRaises(ValueError):
            code_summary(curve)

    def test_draws_must_cover_all_operating_points_and_power(self):
        row = {"failed":[],"power_uW":20,"signed_diff_error_mV":.1,"cm_error_mV":2}
        cases = {s:{str(i):[row.copy() for _ in range(3)] for i in range(32)} for s in range(6)}
        cases[4]["17"][2]["power_uW"]=26
        self.assertEqual(score(cases)["passed"],31)
        cases[4]["17"].pop()
        with self.assertRaises(ValueError):
            score(cases)


class TrimHardware(unittest.TestCase):
    def test_two_ladders_and_cmos_controls_in_editable_dut(self):
        base = dict(length_um=1.5,input=1.3,tail=2.75,load=5.05,driver=14.75,sink=4.1,reference=1.35,
                    bias_ohm=1075000,null_ohm=18905,plate_um=27.5,cm_input=.45,cm_length_um=3.75)
        _,original = sources("example",base)
        for bank in BANKS.values():
            sizes = dict(base,source_trim=bank,paired_geometry=GEOMETRY)
            script,design = sources("example",sizes)
            self.assertIn("fdReadTrimCDF()",script)
            for cell in (INPUT,CORE,PROBED):
                self.assertEqual(set(interfaces(sizes)[cell])-set(INTERFACE[cell]),{n for n,_ in CODE_PINS})
                self.assertTrue(all(interfaces(sizes)[cell][n][2]==(0,-1) for n,_ in CODE_PINS))
            for cell in (BIAS,OUTPUT):
                self.assertEqual(design[cell],original[cell])
            self.assertEqual(len(design[INPUT])-len(original[INPUT]),24)
            for bit,(gate,_) in enumerate(CODE_PINS):
                i = str(bit)
                self.assertEqual(design[TB]["EB"+i]["parameters"]["egain"],bit_gain(8,bit))
                for kind,supply in (("P","VDD"),("N","VSS")):
                    inv = design[INPUT]["MIV"+kind+i]["terms"]
                    self.assertEqual(inv,dict(G=gate,D="TB"+i,S=supply,B=supply))
                for side,control in (("P",gate),("N","TB"+i)):
                    key = "S"+side
                    lo = "TAIL" if bit==3 else key+str(bit+1)
                    self.assertEqual(design[INPUT]["MSW"+side+i]["terms"],dict(G=control,D=key+i,S=lo,B="VSS"))
                    self.assertEqual(design[INPUT]["RS"+side+i]["terms"],dict(PLUS=key+i,MINUS=lo))
                    self.assertEqual(design[INPUT]["MIN"+side]["terms"]["S"],key+"0")
        self.assertIs(interfaces(base),INTERFACE)

    def test_four_bits_and_mismatch_masks_are_applied_to_exact_instances(self):
        bench = "".join("EB{} (T{} 0 VDD 0) vcvs gain={}\n".format(i,i,bit_gain(8,i)) for i in range(4))
        self.assertIn("EB2 (T2 0 VDD 0) vcvs gain=1",applied(bench,5))
        with self.assertRaises(ValueError):
            applied(bench.replace("EB3","EB4"),5)
        full = "".join("{} (d g s b) n18_ckt mismod=1\n".format(n.split(".")[-1]) for n in OP_DEVICES+EXTRA_MOS)
        self.assertEqual(mask_mismatch(full,"all",EXTRA_MOS),full)
        for arm,names in CM_GROUPS.items():
            enabled = [line.split()[0] for line in mask_mismatch(full,arm,EXTRA_MOS).splitlines() if "mismod=1" in line]
            self.assertEqual(set(enabled),set(names))
        with self.assertRaises(ValueError):
            mask_mismatch(full,"all")
        self.assertEqual(group_for("XDUT.XI.RSN2.R0"),"trim_resistors")
        self.assertEqual(group_for("XDUT.XI.MSWP3"),"trim_switches")
        self.assertEqual(group_for("XDUT.XI.MIVN1"),"trim_inverters")
        with self.assertRaises(ValueError):
            group_for("XDUT.XI.MSWP4")
