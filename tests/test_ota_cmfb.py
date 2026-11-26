"""The diagnostic fixture must isolate the sensor and a repair must affect exactly four MOS geometries."""
import copy
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

CASE = Path(__file__).resolve().parents[1]/"examples/smic18-ota-sizing"
sys.path.insert(0,str(CASE))
import ota_cmfb
from ota_native import hierarchy
from ota_views import CMFB, PROBED, sources


class CommonModeDetector(unittest.TestCase):
    native = ("subckt FDOTA18_CMFB BN OUTN OUTP PG VCM VDD VSS\n"
              "MDETP (PG OUTP TCP VSS) n18 w=1u\nends FDOTA18_CMFB\n"
              "subckt FDOTA18_OTA_STB INP INN OUTP OUTN VDD VSS\n"
              "XC (BN OUTN OUTP CMG VCM VDD VSS) FDOTA18_CMFB\n"
              "XO (OUTP OUTN VDD VSS) OUTPUT\nPCM (CMG PG) iprobe\nends FDOTA18_OTA_STB\n"
              "XDUT (INP INN OUTP OUTN VDD 0) FDOTA18_OTA_STB\n")

    def test_ideal_mean_changes_only_the_two_sensing_ports(self):
        before,bench = hierarchy(self.native)
        after,new_bench = hierarchy(ota_cmfb.averaged_detector(self.native))
        self.assertEqual(bench,new_bench)
        self.assertEqual(before[CMFB],after[CMFB])
        rest = after[PROBED][1].copy()
        self.assertEqual(rest.pop("EAVG1"),(["CMAVG_HALF","VSS","OUTP","VSS"],"vcvs",{"gain":"0.5"}))
        self.assertEqual(rest.pop("EAVG2"),(["CMAVG","CMAVG_HALF","OUTN","VSS"],"vcvs",{"gain":"0.5"}))
        self.assertEqual(rest.pop("XC")[0],["BN","CMAVG","CMAVG","CMG","VCM","VDD","VSS"])
        self.assertEqual(rest,{n:v for n,v in before[PROBED][1].items() if n!="XC"})
        for wrong in (self.native.replace("XC (BN OUTN OUTP","XC (BN OUTP OUTN"),
                      ota_cmfb.averaged_detector(self.native)):
            with self.assertRaises(ValueError):
                ota_cmfb.averaged_detector(wrong)

    def test_only_the_sensor_and_reference_devices_change_geometry(self):
        sizes = dict(length_um=1.5,input=1.3,tail=2.75,load=5.05,driver=14.75,sink=4.1,reference=1.35,
                     bias_ohm=1075000,null_ohm=18905,plate_um=27.5)
        script,old = sources("example",sizes)
        self.assertEqual(script,sources("example",dict(sizes,cm_input=1.3,cm_length_um=1.5))[0])
        _,new = sources("example",dict(sizes,cm_input=.45,cm_length_um=3.0))
        expected = copy.deepcopy(old)
        for name in ("MDETP","MDETN","MCREFP","MCREFN"):
            expected[CMFB][name]["parameters"].update(fw="0.45u",l="3u")
        self.assertEqual(expected,new)

    def test_a_mismatched_quiescent_control_cannot_be_used_for_attribution(self):
        row = dict(command_V=0,output_p_V=.91,output_n_V=.91,control_V=1.25,current_uA={"MCTP":1.1})
        ota_cmfb.check_zero_command(row,dict(row))
        for other in (dict(row,command_V=.3),dict(row,control_V=1.251),
                      dict(row,current_uA={"MCTP":1.111}),dict(row,current_uA={})):
            with self.assertRaises(ValueError):
                ota_cmfb.check_zero_command(row,other)

    def test_boundary_refinement_requires_connected_coarse_points_and_both_fine_directions(self):
        points = [dict(command_V=(i-40)*.0075,output_p_V=.9+(i-40)*.00375,output_n_V=.9-(i-40)*.00375,
                       cm_error_mV=0,tracking_mV=0,headroom_V=.1,passed=True) for i in range(81)]
        interval = [-.2925,.2925]
        self.assertTrue(ota_cmfb.requested_range(interval,1.8,points,points)["passed"])
        self.assertFalse(ota_cmfb.requested_range([-.27,.2925],1.8,points,points)["passed"])
        failed = copy.deepcopy(points)
        failed[17]["passed"]=False
        self.assertFalse(ota_cmfb.requested_range(interval,1.8,points,failed)["passed"])
        shifted = copy.deepcopy(points)
        shifted[-1]["output_p_V"]+=.0006
        self.assertFalse(ota_cmfb.requested_range(interval,1.8,points,shifted)["passed"])
        for missing in (points[:-1],list(reversed(points))):
            with self.assertRaises(ValueError):
                ota_cmfb.requested_range(interval,1.8,points,missing)

    def test_delivery_launcher_opens_the_accepted_stage_only(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            ota_cmfb.accepted_launcher(root,None,True)
            self.assertFalse((root/"open.sh").exists())
            ota_cmfb.accepted_launcher(root,"midpoint/open.sh",True)
            self.assertIn("midpoint", (root/"open.sh").read_text())
            with self.assertRaises(ValueError):
                ota_cmfb.accepted_launcher(root,None,False)
            with self.assertRaises(ValueError):
                ota_cmfb.accepted_launcher(root,"open-first.sh",False)
