"""Regressions for physical mismatch pairing and full-scale common-mode recovery."""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

CASE = Path(__file__).resolve().parents[1]/"examples/smic18-ota-sizing"
sys.path.insert(0,str(CASE))
import ota_robust
from ota_measure import OP_DEVICES
from ota_mismatch import ARMS, coverage, mask_mismatch, sampled_ids


class Mismatch(unittest.TestCase):
    def test_diagnostic_masks_change_only_the_intended_physical_mos(self):
        names = [path.split(".")[-1] for path in OP_DEVICES]
        lines = ["{} (d g s b) n18_ckt w=1u mismod=(1) mr=1".format(name) for name in names]
        original = "\n".join(lines+["RB (d b) resistor r=5k"])
        branches = dict(all=set(names),off=set(),input_pair={"MINP","MINN"},
                        cmfb={path.split(".")[-1] for path in OP_DEVICES if ".XC." in path})
        for arm in ARMS:
            changed = mask_mismatch(original,arm)
            active = {line.split()[0] for line in changed.splitlines() if "mismod=(1)" in line}
            self.assertEqual(active,branches[arm])
            self.assertEqual(changed.replace("mismod=0","mismod=(1)"),original)
        for wrong in (original.replace("n18_ckt","n18",1),original+"\n"+lines[0],
                      original.replace("mismod=(1)","mismod=0",1)):
            with self.assertRaises(ValueError):
                mask_mismatch(wrong,"all")

    def test_physical_scope_rows_are_checked_before_pairing(self):
        with TemporaryDirectory() as folder:
            desc,data = (Path(folder)/name for name in ("mcparam","mcdata"))
            desc.write_text("a b\n"+"\n".join(OP_DEVICES)+"\n")
            blocks = [["{} {}".format(i,j) for i in range(len(OP_DEVICES))] for j in range(2)]
            def write(rows):
                data.write_text("".join("#{}\n{}\n".format(i,"\n".join(row)) for i,row in enumerate(rows,1)))
            write(blocks)
            ids,width = sampled_ids(desc,data,2)
            self.assertEqual(width,2)
            self.assertEqual(len(set(ids)),2)
            bad_rows = ([blocks[0],blocks[0]], [blocks[0],blocks[1][:-1]],
                        [blocks[0],["nan 1"]+blocks[1][1:]], [blocks[0],[blocks[0][0]]+blocks[1][1:]],
                        [blocks[0],["0","1 1 1"]+blocks[1][2:]])
            for wrong in bad_rows:
                write(wrong)
                with self.assertRaises(ValueError):
                    sampled_ids(desc,data,2)
            write(blocks)
            desc.write_text(desc.read_text().replace(OP_DEVICES[0],"UNKNOWN"))
            with self.assertRaises(ValueError):
                sampled_ids(desc,data,2)

    def test_each_draw_must_pass_every_condition_with_identical_numeric_identity(self):
        def batch(failures):
            return dict(ids=["a","b"],descriptor_sha256="fields",samples=[[dict(failed=f)] for f in failures])
        a,b = batch([["common"],[]]),batch([[],["tracking"]])
        result = coverage(dict(low=a,high=b))
        self.assertEqual(result["passed"],0)
        self.assertEqual(result["passed_per_gate"]["tracking"],1)
        self.assertEqual(result["passed_per_gate"]["supply"],2)
        for wrong in (dict(b,ids=["b","a"]),dict(b,descriptor_sha256="other"),dict(b,samples=b["samples"][:1])):
            with self.assertRaises(ValueError):
                coverage(dict(low=a,high=wrong))


class FullScale(unittest.TestCase):
    def test_plotted_average_is_bound_to_both_physical_outputs(self):
        from ota_ade import TRAN_OUTPUTS,check_transient_outputs
        test = ET.Element("Test",Name="FF_FULL_950")
        for label,expression in TRAN_OUTPUTS.items():
            item = ET.SubElement(test,"field",Type="defstruct")
            ET.SubElement(item,"field",Name="name").text = '"'+label+'"'
            ET.SubElement(item,"field",Name="expression").text = expression
        check_transient_outputs(test)
        test.findall(".//field[@Name='expression']")[1].text = '(VT("/OUTP") + VT("/OUTP")) / 2'
        with self.assertRaises(ValueError):
            check_transient_outputs(test)

    def test_native_full_scale_bench_only_changes_the_stimulus(self):
        original = ("EVIC (a b c d) vcvs gain=0.6\nVD (cmd 0) vsource val0=-50m val1=50m\n"
                    "VOFF (off 0) vsource val1=20m\nCLP (out 0) capacitor c=2p\n")
        large = original.replace("gain=0.6","gain=ICMGAIN").replace("50m","300m").replace("20m","0")
        ota_robust.check_fullscale_bench(original,large)
        for wrong in (large.replace("2p","1p"),large.replace("300m","50m",1),large.replace("(out 0)","(cmd 0)")):
            with self.assertRaises(ValueError):
                ota_robust.check_fullscale_bench(original,wrong)

    def test_maestro_preserves_nominal_tests_and_binds_six_full_scale_inputs(self):
        from ota_ade import test_groups
        states = ota_robust.STATES
        regular = list(test_groups(states,()))
        self.assertEqual(sum(len(row[3]) for row in regular),15)
        self.assertTrue(all("ICMGAIN" not in variables for _,_,_,tests in regular for _,variables in tests.values()))
        trials = list(test_groups(states,(.95,1.10)))
        self.assertEqual(len({r[0] for r in trials}),6)
        for _,(corner,_,vdd),icm,tests in trials:
            self.assertEqual(set(tests),{"tran"})
            name,variables = tests["tran"]
            self.assertEqual(name,"{}_FULL_{}".format(corner.upper(),round(icm*1000)))
            self.assertEqual(variables["BETA"],"1")
            self.assertAlmostEqual(vdd*float(variables["ICMGAIN"]),icm)

    def fixture(self):
        t = [i*2e-9 for i in range(5001)]
        vdd = [1.8*min(1,x/1e-6) for x in t]
        cmd = [ota_robust.commanded(x) for x in t]
        return dict(time=t,VDD=vdd,VCM=[x/2 for x in vdd],CMD=cmd,
                    INP=[.95*x/1.8 for x in vdd],INN=[.95*x/1.8 for x in vdd],
                    OUTP=[x/2+y/2 for x,y in zip(vdd,cmd)],OUTN=[x/2-y/2 for x,y in zip(vdd,cmd)])

    def test_last_excursion_in_either_direction_determines_settling(self):
        data = self.fixture()
        with patch.object(ota_robust,"samples",return_value=data):
            self.assertTrue(ota_robust.fullscale_metrics(None,1.8,.95)["passed"])
            for name in ("OUTP","OUTN"):
                data[name][2550]+=.031
                data[name][-1]-=.032
            result = ota_robust.fullscale_metrics(None,1.8,.95)
            self.assertTrue(result["startup_passed"])
            self.assertFalse(result["passed"])
            self.assertAlmostEqual(result["directions"]["rising"]["joint_settling_us"],1.102)
            self.assertIsNone(result["directions"]["falling"]["joint_settling_us"])

    def test_changed_input_common_or_stimulus_invalidates_a_good_response(self):
        for signal in ("CMD","INN","VCM","VDD"):
            data = self.fixture()
            data[signal][2200]+=.03
            with patch.object(ota_robust,"samples",return_value=data), self.assertRaises(ValueError):
                ota_robust.fullscale_metrics(None,1.8,.95)
