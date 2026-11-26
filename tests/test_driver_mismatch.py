"""Guard the sampling-driver mismatch controls, signed offset and joint sample limits."""
from pathlib import Path
import sys
import tempfile
import unittest

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/sky130-adc-driver"
sys.path.insert(0, str(EXAMPLE))
sys.path.insert(1, str(EXAMPLE.parent/"sky130-current-mirror"))
from mismatch_bench import DEVICES, METRICS, draw, offset, pair_response, summarize, trial_names, wrap_native


class DriverMismatch(unittest.TestCase):
    def test_each_group_has_local_draws_and_both_multipliers(self):
        values = draw(37)
        self.assertEqual(values, draw(37))
        self.assertNotEqual(values, draw(38))
        self.assertNotEqual(values["XIP"], values["XIM"])
        self.assertTrue(all(v == 0 for knobs in draw(None).values() for v in knobs.values()))
        lines = [n+" "+pins+" sky130_fd_pr__"+pol+"fet_01v8 w=6 l=1 m=3" for n,(pins,pol) in DEVICES.items()]
        generated = wrap_native("\n".join(lines+[".end"]), values)
        self.assertIn("XIP VX INP TAIL VSS MM_XIP", generated)
        self.assertIn("Xdevice d g s b sky130_fd_pr__nfet_01v8 w=6 l=1 m=3 mult=3", generated)
        self.assertEqual(generated.count(".subckt MM_"), len(DEVICES))
        with self.assertRaises(ValueError):
            wrap_native("\n".join(lines[1:]+[".end"]), values)
        trials = trial_names(37, 2)
        self.assertEqual([n for n,_,_ in trials], ["reference","zero","seed-37","seed-38","repeat"])
        self.assertEqual(trials[2][2],trials[-1][2])

    def test_shared_mismatch_and_wrong_effective_area_fail_calibration(self):
        sizing = {"devices":{"XIP":[6,1,4], "XLA":[7.5,1,9]}}
        names, currents = [], []
        for pol,root in (("n",2),("p",3)):
            for mode,diff in (("zero",0),("split",.01),("swap",-.01),("unit",root*.01)):
                for i in (0,1):
                    names.append("i(v{}{}{})".format(pol,mode,i))
                    currents.append(1e-5*(1+diff*(.5-i)))
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"pair.dat"
            def write(data):
                path.write_text("d "+" ".join(names)+"\n.65 "+" ".join(map(str,data))+"\n")
            write(currents)
            self.assertAlmostEqual(pair_response(path,sizing)["p"]["unit_to_native_ratio"],3)
            write([1e-5]*len(currents))
            with self.assertRaisesRegex(ValueError,"Unresolved"):
                pair_response(path,sizing)
            write(currents)
            with self.assertRaisesRegex(ValueError,"Area scaling"):
                pair_response(path,{"devices":{"XIP":[6,1,1],"XLA":[7.5,1,9]}})

    def test_input_offset_has_sign_and_uses_the_feedback_node(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"balance.dat"
            def write(shift, wrong_feedback=0):
                lines=["v-sweep v(vin) v(out) v(fb)"]
                for k in range(81):
                    vin=.91+k*.001
                    vout=.95+(vin-.95-shift)*.99
                    lines.append("{} {} {} {}".format(vin,vin,vout,vout+wrong_feedback))
                path.write_text("\n".join(lines))
            spec={"input_low_V":.85,"input_high_V":1.05}
            for shift in (-.0016,.0025):
                write(shift)
                self.assertAlmostEqual(offset(path,spec),1000*shift)
            write(.001,.005)
            with self.assertRaisesRegex(ValueError,"feedback"):
                offset(path,spec)
            write(.10)
            with self.assertRaisesRegex(ValueError,"bracket"):
                offset(path,spec)

    def test_joint_count_requires_all_limits_on_the_same_draw(self):
        spec={"full_scale_span_V":1, "adc_bits":7, "input_low_V":.85, "input_high_V":1.05, "track_ns":100,"sample_cap_pF":1}
        nominal=dict.fromkeys(METRICS,2.0)
        nominal["input_offset_mV"]=.1
        a=dict(nominal,seed=1,up_track_mV=4.0)
        b=dict(nominal,seed=2,down_hold_mV=4.1,input_offset_mV=-.2)
        summary=summarize([a,b],nominal,spec)
        self.assertEqual(summary["within_all_four"],0)
        self.assertEqual(summary["within_each"]["up_track_mV"],1)
        self.assertEqual(summary["within_each"]["down_hold_mV"],1)
        with self.assertRaisesRegex(ValueError,"seed"):
            summarize([a,a],nominal,spec)
        with self.assertRaisesRegex(ValueError,"resolved"):
            summarize([a,dict(b,input_offset_mV=.1)],nominal,spec)
