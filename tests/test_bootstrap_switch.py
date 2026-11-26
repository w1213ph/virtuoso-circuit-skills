"""Protect held-sample THD, startup stress polarity and the outward-facing parent wires."""
import math
from pathlib import Path
import sys
import unittest

CASE=Path(__file__).resolve().parents[1]/"examples/sky130-bootstrap-switch"
sys.path.insert(0,str(CASE))
from switch_design import CYCLES, TONE_BIN, circuit, requirements
from switch_measure import harmonics, stress
from switch_views import parent_check, view_sources
from switch import largest_terminal_failure, refined_limits


class BootstrapSwitch(unittest.TestCase):
    def test_harmonics_use_held_amplitudes_and_exclude_dc(self):
        phase=[2*math.pi*TONE_BIN*k/CYCLES for k in range(CYCLES)]
        bins,thd=harmonics([.95+.5*math.sin(t)+.0005*math.cos(3*t) for t in phase])
        self.assertAlmostEqual(bins[0],.5)
        self.assertAlmostEqual(thd,-60.)
        with self.assertRaises(ValueError):harmonics([0.]*CYCLES)

    def test_signed_well_range_and_engineering_budget_have_distinct_results(self):
        d=dict(side_um=0,devices={"XPRE":("VDD GATE TOP WELL","P5",{})})
        t=[0.,1.,2.];v={"vdd":[0.,1.8,1.8],"gate":[0.,0.,2.6],"top":[0.,1.8,2.6],"well":[0.,1.64,2.6]}
        terminals,_,bad,dc,outside=stress(t,v,d,.2)
        self.assertEqual(bad,[])
        self.assertAlmostEqual(dc["XPRE"]["excess_V"],.16)
        self.assertAlmostEqual(outside["XPRE.BS"]["time_ns"],1.)
        self.assertAlmostEqual(terminals["XPRE"]["BD"]["min_V"],-.16)
        v["well"][1]=1.50
        signed,plates,bad,_,_=stress(t,v,d,.2)
        self.assertEqual(set(bad),{"XPRE.BS","XPRE.BD"})
        failure=largest_terminal_failure({"violations":bad,"terminals":signed,"plates":plates},d,.2)
        self.assertAlmostEqual(failure["excess_V"],.10)
        self.assertAlmostEqual(failure["observed_V"],-.30)
        self.assertEqual(failure["time_ns"],1.)

    def test_refined_report_uses_the_last_measured_step(self):
        def row(peak):
            return {"max_forward_vbs":{"device":"XLINK","excess_V":peak},"published_excursion":{"terminal":"XLINK.BS","excess_V":peak}}
        forward,model=refined_limits({"cases":{"ss-swing2":[row(.196),row(.214)],"tt-swing2":[row(.16)]}})
        self.assertEqual((forward["case"],forward["excess_V"]),("ss-swing2",.214))
        self.assertEqual((model["cases_outside"],model["worst"]["excess_V"]),(2,.214))

    def test_compact_plate_counts_both_capacitors_and_bottom_pin_leaves_down(self):
        d=circuit("compact",16)
        self.assertEqual(d["plate_um2"],19**2+3.5**2)
        views=view_sources(d,requirements(CASE/"requirements.example.json"));parent_check(views)
        views["TB_BSW_SAMPLE.sch"]=views["TB_BSW_SAMPLE.sch"].replace("N 400 290 400 345 {lab=CLKB}","N 400 290 400 265 {lab=CLKB}")
        with self.assertRaisesRegex(ValueError,"outward"):parent_check(views)
