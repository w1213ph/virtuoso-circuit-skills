"""Regression tests for signed headroom, fixed-bias interpolation and the measured OTA examples."""
import csv
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / "examples/smic18-ota-sizing"
sys.path[:0] = [str(CASE), str(ROOT / "skills/paper-to-virtuoso/scripts")]
from device_headroom import bias_at_current, crosscheck, lookup_curve, margin_change, read_curves, saturation_margin
from ota import config_for_site
from ota_recovery import DESIGNS
from ota_trim import BANKS, GEOMETRY
from ota_views import BIAS, CMFB, sources
from replay_headroom import compare


class Headroom(unittest.TestCase):
    def test_margin_and_required_voltage_have_distinct_effects(self):
        change = margin_change(-.1, -.11, -.135, -.12)
        for field,value in (("before_V",-.01),("after_V",.015),("drain_change_V",.035),
                            ("required_change_V",.01),("gain_V",.025)):
            self.assertAlmostEqual(change[field],value)
        with self.assertRaises(ValueError):
            saturation_margin(math.nan, .1)

    def test_interpolate_only_the_conducting_measured_branch(self):
        gates, currents, gm = [0,.1,.2,.3], [0,1e-7,1e-6,1e-5], [0,1e-6,1e-5,1e-4]
        row = bias_at_current(gates, currents, gm, math.sqrt(1e-13))
        self.assertAlmostEqual(row["vgs_V"],.15)
        self.assertAlmostEqual(row["gm_id_per_V"],10)
        self.assertEqual(row["bracket_V"],[.1,.2])
        for i in (1,3):
            self.assertEqual(bias_at_current(gates,currents,gm,currents[i])["vgs_V"],gates[i])
        for g,i,m,target in ((gates,currents,gm,1e-8),(gates,currents,gm,2e-5),
                ([0,.1,.1,.3],currents,gm,1e-6),(gates,[1,4,2,5],[1]*4,3),(gates,[1,2,2,3],[1]*4,2),
                (gates,currents,[0,1,2,float("nan")],1e-6)):
            with self.assertRaises(ValueError):
                bias_at_current(g,i,m,target)

    def test_actual_slow_corner_lookup_and_held_out_points(self):
        curves = read_curves(CASE/"headroom-sweep.csv")
        bias = (.982794789601, -.114839580384)
        old = lookup_curve(curves, ("N",3.75,.45,*bias), .732675739408)
        new = lookup_curve(curves, ("N",3.50,.50,*bias), .732675739408)
        self.assertAlmostEqual(1000*(old["vgs_V"]-new["vgs_V"]),21.58,delta=.02)
        self.assertAlmostEqual(old["gm_id_per_V"],7.18,delta=.02)
        check = crosscheck(curves)
        self.assertEqual((check["curves"],check["withheld_points"]),(2,71))
        self.assertLess(check["max_gate_error_mV"],.17)
        with self.assertRaises(ValueError):
            lookup_curve(curves,("N",3.50,.50,.982794789601,0),.732675739408)

    def test_reject_mixed_corner_or_ambiguous_units(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"mos.csv"
            first=(CASE/"headroom-sweep.csv").read_text().splitlines()[:4]
            for lines in ([first[0].replace("abs_id_uA","abs_id_A"),*first[1:]], [*first,first[2].replace("ss,","ff,")]):
                path.write_text("\n".join(lines))
                with self.assertRaises(ValueError):
                    read_curves(path)
            with self.assertRaises(ValueError):
                crosscheck({("P",):[(0,1e-9,1e-9),(.2,1e-6,1e-6),(.1,1e-5,1e-5)]})

    def test_all_held_die_measurements_recover_both_tails(self):
        for name,margin in (("geometry",5.423515),("bias",9.774404)):
            result=compare(CASE/"headroom-points.csv",name)
            self.assertEqual(result["negative_points"],{"baseline":40,"candidate":0})
            self.assertEqual((result["recovered"],result["introduced"],result["points"]),(40,0,1152))
            self.assertEqual(result["samples_with_both_tails_saturated"],{"baseline":18,"candidate":32})
            self.assertAlmostEqual(result["worst"]["candidate"]["margin_mV"],margin,places=5)

    def test_missing_repeated_or_recoded_sample_is_invalid(self):
        raw=(CASE/"headroom-points.csv").read_text().splitlines()
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"points.csv"
            recoded=next(csv.reader([raw[2]]))
            recoded[1]=str((int(recoded[1])+1)%16)
            for lines in (raw[:-1],[*raw,raw[-1]], [*raw[:2],",".join(recoded),*raw[3:]]):
                path.write_text("\n".join(lines))
                with self.assertRaises(ValueError):
                    compare(path)

    def test_dimensions_apply_only_to_four_sensor_devices_and_reference(self):
        sizes=dict(length_um=1.5,plate_um=27.5,branch_uA=1.2,input=2,load=4,driver=6,sink=3,
                   reference=1.35,tail=2.7,bias_ohm=900000,null_ohm=17000,cm_input=.45,cm_length_um=3.75,
                   paired_geometry=GEOMETRY,source_trim=BANKS["centered"])
        _,base=sources("process",dict(sizes,**DESIGNS["r130"]))
        _,final=sources("process",dict(sizes,**DESIGNS["sense-bias140"]))
        changes=set()
        for cell, devices in base.items():
            self.assertEqual(set(devices),set(final[cell]))
            for name,device in devices.items():
                other=final[cell][name]
                self.assertEqual(device["terms"],other["terms"])
                if device["parameters"]!=other["parameters"]:
                    changes.add((cell,name))
                    wh=("1.4u","1.5u") if cell==BIAS else ("0.5u","3.5u")
                    self.assertEqual(dict(device["parameters"],fw=wh[0],l=wh[1]),other["parameters"])
        self.assertEqual(changes,{(BIAS,"MREF")}|{(CMFB,n) for n in ("MDETP","MDETN","MCREFP","MCREFN")})

    def test_one_point_calibration_and_static_code_bound_remain_distinct(self):
        report=json.loads((CASE/"headroom-review.json").read_text())
        rows=report["empirical_samples"]
        self.assertEqual({r["sample"] for r in rows},set(range(1,33)))
        for row in rows:
            bounds=row["static_code_bounds"]
            self.assertEqual(row["code"],bounds["held"])
            self.assertEqual(not row["failed"],row["code"] in bounds["passing"])
            self.assertTrue(set(bounds["passing"])<=set(bounds["tracking"]) & set(bounds["common"]))
        self.assertEqual(sum(not r["failed"] for r in rows),report["decision"]["calibrated_passed"])
        self.assertEqual(sum(bool(r["static_code_bounds"]["passing"]) for r in rows),report["code_bounds"]["passing"])
        self.assertEqual((report["decision"]["calibrated_passed"],report["code_bounds"]["passing"]),(23,27))
