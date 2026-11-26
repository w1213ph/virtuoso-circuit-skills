import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
CASE=ROOT/"examples/sky130-adc-driver"
sys.path.insert(0,str(ROOT/"examples/sky130-current-mirror"))
sys.path.insert(0,str(CASE))
import bench
import driver
import precision
from driver_review import limiting_measurements


class SamplingDriverTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp=Path(tmp.name)
        self.req=bench.requirements(CASE/"requirements.example.json")

    def test_adc_span_bits_and_capacitance_have_distinct_roles(self):
        original=bench.budget(self.req)
        tighter=bench.budget(dict(self.req,adc_bits=8))
        loaded=bench.budget(dict(self.req,sample_cap_pF=2.6))
        self.assertEqual(original["half_lsb_mV"],3.90625)
        self.assertAlmostEqual(original["step_V"]*math.exp(-100/original["tau_max_ns"]),3.90625e-3)
        self.assertEqual(tighter["half_lsb_mV"],original["half_lsb_mV"]/2)
        self.assertLess(tighter["tau_max_ns"],original["tau_max_ns"])
        self.assertEqual(loaded["tau_max_ns"],original["tau_max_ns"])
        self.assertAlmostEqual(loaded["gm_floor_uS"],2*original["gm_floor_uS"])
        missing=dict(self.req)
        missing.pop("sample_cap_pF")
        for bad in (missing,dict(self.req,adc_bits=True),dict(self.req,track_ns=0.1)):
            f=self.tmp/"invalid.json"
            f.write_text(json.dumps(bad),encoding="utf-8")
            with self.assertRaises(ValueError):
                bench.requirements(f)

    def test_both_parent_symbols_have_outward_leads(self):
        for name in ("TB_ADRV_SAMPLE.sch","ADRV_OTA.sym","ADRV_SAMPLER.sym"):
            (self.tmp/name).write_bytes((CASE/name).read_bytes())
        pins=driver.parent_pins(self.tmp)
        self.assertEqual(len(pins),11)
        f=self.tmp/"TB_ADRV_SAMPLE.sch"
        f.write_text(f.read_text().replace("755 360 755 415","755 360 755 315"))
        with self.assertRaisesRegex(ValueError,"CLKB"):
            driver.parent_pins(self.tmp)

    def test_changed_editable_capacitor_invalidates_current_and_historical_results(self):
        sizing = {"devices": {name:(2, .5, 1) for name in (*driver.OTA, *driver.SAMPLER)}}
        driver.materialize(self.tmp, sizing, self.req)
        self.assertEqual(len(driver.checked_views(self.tmp, sizing, self.req)), 8)
        cap = self.tmp / "ADRV_SAMPLER.sch"
        source = cap.read_text(encoding="utf-8")
        cap.write_text(source.replace("name=CSH value=1p", "name=CSH value=2p"), encoding="utf-8")
        for legacy in (False, True):
            with self.subTest(legacy=legacy), self.assertRaisesRegex(ValueError, "ADRV_SAMPLER.sch"):
                driver.checked_views(self.tmp, sizing, self.req, legacy=legacy)

    def test_worst_aperture_hold_and_loop_can_come_from_different_cases(self):
        def case(track, hold, down, track_pm, hold_pm):
            return {"transient":{"edges":{"up":{"track_error_mV":track,"hold_error_mV":hold},
                                          "down":{"track_error_mV":.1,"hold_error_mV":down}}},
                    "track":{"phase_margin_deg":track_pm},"hold":{"phase_margin_deg":hold_pm}}
        rows = {"ss":case(4, .2, .6, 70, 72), "ff":case(2, .3, 5, 80, 65)}
        self.assertEqual(limiting_measurements(rows), (("ss","up",4),("ff","down",5),("ff","hold",65)))

    def test_dc_screen_uses_all_nine_signed_capacitor_levels_and_checked_clock(self):
        def write(clkb, count):
            rows = ["v-sweep " + " ".join(precision.DC_VECTORS)]
            for i in range(count):
                v = .85 + .025*i
                rows.append("{} {} {} {} 1.62 {} 1.62".format(v,v,v,v-(.003 if i==4 else .001),clkb))
            (self.tmp/"dc.dat").write_text("\n".join(rows))
        write(0,9)
        r=precision.dc_transfer(self.tmp,self.req,1.62)
        self.assertAlmostEqual(r["worst_input_V"],.95)
        self.assertAlmostEqual(r["points"][4]["signed_error_mV"],-3)
        self.assertAlmostEqual(r["worst_abs_mV"],3)
        write(1.62,9)
        with self.assertRaisesRegex(ValueError,"clock"):
            precision.dc_transfer(self.tmp,self.req,1.62)
        write(0,8)
        with self.assertRaisesRegex(ValueError,"nine"):
            precision.dc_transfer(self.tmp,self.req,1.62)

    def test_switch_resizing_keeps_the_sampling_fixture_and_counts_gate_area(self):
        base={"devices":{n:(6,1,2) for n in (*driver.OTA,*driver.SAMPLER)}}
        adjusted=precision.modified(base,extra=2,switch=2)
        self.assertEqual(adjusted["devices"]["XLA"],(1.5,1,8))
        self.assertEqual(adjusted["devices"]["XLB"],(1.5,1,10))
        self.assertEqual(adjusted["switch_gate_area_um2"],48)
        self.assertEqual(base["devices"]["XLA"],(6,1,2))
        sampler={"CSH":["SAMPLE","VSS","1.25P","M=1"],"XSN":["SAMPLE","CLK","IN","VSS","N","M=1"],
                 "XSP":["SAMPLE","CLKB","IN","VDD","P","M=1"]}
        original={"TOP":{"COUT":["OUT","0","0.6P"]},"ADRV_SAMPLER":sampler}
        changed={"TOP":original["TOP"],"ADRV_SAMPLER":dict(sampler,XSN=sampler["XSN"][:-1]+["M=2"])}
        self.assertTrue(precision.same_fixture(changed,original,True))
        self.assertFalse(precision.same_fixture(changed,original,False))
        changed["ADRV_SAMPLER"]["CSH"]=["SAMPLE","VSS","500F","M=1"]
        self.assertFalse(precision.same_fixture(changed,original,True))

    def test_loop_uses_loaded_state_and_rejects_multiple_crossings(self):
        op=self.tmp/"op.dat"
        op.write_text("scale v(vdd) v(clk) v(clkb) v(vin) v(out) v(xdrv.vb) i(vvdd)\n0 1.8 1.8 0 .95 .948 .72 -30e-6\n")
        f=self.tmp/"ac.dat"
        def curve(mids):
            f.write_text("frequency loop loop\n10 100 0\n1e6 0 -10\n"+mids+"1e10 -.001 -.001\n")
        curve("1e8 0 -.1\n")
        m=bench.loop_metrics(self.tmp,1.8,True,self.req)
        self.assertAlmostEqual(m["unity_MHz"],10)
        self.assertAlmostEqual(m["phase_margin_deg"],90)
        with self.assertRaisesRegex(ValueError,"operating point"):
            bench.loop_metrics(self.tmp,1.8,False,self.req)
        curve("1e8 0 -.1\n1e9 0 -2\n")
        with self.assertRaisesRegex(ValueError,"one descending"):
            bench.loop_metrics(self.tmp,1.8,True,self.req)

    def test_held_capacitor_not_driver_output_determines_both_edges(self):
        r=self.req
        intervals,stop=bench.schedule(r)
        levels=(r["input_low_V"],r["input_high_V"])
        def ramp(t,a):
            return min(1,max(0,t-a))
        rows=["time "+" ".join(bench.TRACE)+"\n"]
        for i in range(int(stop*4)+1):
            t=i*.25
            rail=1.8*min(1,t/100)
            clk=1.8*sum(ramp(t,a)-ramp(t,b) for a,b in ((300,600),*intervals))
            vin=levels[0]*min(1,t/100)+(levels[1]-levels[0])*(ramp(t,800)-ramp(t,1100))
            # Driver follows perfectly; only the capacitor misses the rising aperture and later hold.
            cap=vin-(.010 if 899<=t<=900 else .015 if 907<=t<=910 else 0)
            vals=(t*1e-9,vin,vin,cap,clk,rail-clk,rail,.7*min(1,t/100),-10e-6,2e-6)
            rows.append(" ".join("{:.12g}".format(v) for v in vals)+"\n")
        f=self.tmp/"transient.dat"
        f.write_text("".join(rows))
        edges=bench.transient_metrics(self.tmp,r,1.8)["edges"]
        self.assertEqual(edges["up"]["driver_error_mV"],0)
        self.assertAlmostEqual(edges["up"]["switch_drop_mV"],10)
        self.assertAlmostEqual(edges["up"]["track_error_mV"],10)
        self.assertAlmostEqual(edges["up"]["hold_error_mV"],15)
        self.assertAlmostEqual(edges["up"]["hold_endpoint_error_mV"],0)
        self.assertEqual(edges["down"]["hold_error_mV"],0)
        f.write_text("".join(rows[:-100]))
        with self.assertRaisesRegex(ValueError,"too coarse"):
            bench.transient_metrics(self.tmp,r,1.8)
