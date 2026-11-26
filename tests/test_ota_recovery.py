"""Protect settling decisions and the physical scope of the common-mode repair."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/"skills/paper-to-virtuoso/scripts"),str(ROOT/"examples/smic18-ota-sizing")]
from settling_diagnosis import common_components, settling_window
from ota import config_for_site  # Loads the native case dependencies.
from ota_recovery import DESIGNS
from ota_recovery_pair import control_fixture
from ota_native import hierarchy
from ota_trim import BANKS, GEOMETRY
from ota_views import CMFB, OUTPUT, sources
from replay_recovery import compare


class Recovery(unittest.TestCase):
    def test_reentry_and_never_settled(self):
        times=[0,1,2,3,4,5]
        v=[3,0,2,1,-1,4]
        hit=settling_window(times,v,0,1,0,4)
        self.assertEqual((hit["first_entry_s"],hit["permanent_entry_s"],hit["exits_after_entry"]),(1,3,1))
        self.assertEqual(hit["max_error_after_first"],2)
        self.assertIsNone(settling_window(times,v,0,1,0,5)["permanent_entry_s"])
        self.assertIsNone(settling_window(times,[3]*6,0,1,0,5)["first_entry_s"])
        self.assertEqual(settling_window(times,[0]*6,0,1,1,3)["permanent_entry_s"],0)

    def test_truncated_and_unordered_data(self):
        for t,v,lo,hi in (([1,2],[0,0],0,2),([0,1],[0,0],0,2),([0,1,1],[0,0,0],0,1),
                          ([0,1],[0,float("nan")],0,1),([0,1],[0],0,1)):
            with self.assertRaises(ValueError):
                settling_window(t,v,0,.01,lo,hi)
        with self.assertRaisesRegex(ValueError,"sampling gap"):
            settling_window([0,1,3],[2,0,0],0,1,0,3,1)

    def test_command_parity(self):
        self.assertEqual(common_components([-.3,0,.3],[12,5,4]),dict(zero=5,odd=-4,even=3))
        for cmd in ([.3,0,-.3],[-.3,.01,.3],[-.3,0,.29],[-.3,0]):
            with self.assertRaises(ValueError):
                common_components(cmd,[12,5,4])

    def test_public_slow_corner_samples(self):
        result=compare(ROOT/"examples/smic18-ota-sizing/recovery-waves.csv")
        for name,first,held in (("baseline",.679,1.031),("recovery",.621,.967)):
            self.assertAlmostEqual(result[name]["first_entry_s"]*1e6,first,places=6)
            self.assertAlmostEqual(result[name]["permanent_entry_s"]*1e6,held,places=6)
            self.assertEqual(result[name]["exits_after_entry"],1)

    def test_control_arms_keep_device_inventory_and_electrical_polarity(self):
        net=("subckt "+CMFB+" PG RCG VDD\nMCM (PG RCG VDD VDD) p18_ckt mismod=1\n"
             "RREC (RCG PG) rhrpo_pcell_2 segW=1u\nends "+CMFB+"\n")
        for recovered in (False,True):
            block=hierarchy(control_fixture(net,recovered))[0][CMFB][1]
            self.assertEqual(set(block),{"MCM","RREC","EABG","EABR"})
            self.assertEqual(block["MCM"][0],["PG","ABGATE","VDD","VDD"])
            self.assertEqual(block["RREC"][0],["ABRES","PG"])
            self.assertEqual(block["EABG"],(["ABGATE","PG","RCG","PG"],"vcvs",{"gain":str(int(recovered))}))
            self.assertEqual(block["EABR"],(["ABRES","RCG","PG","RCG"],"vcvs",{"gain":str(int(not recovered))}))
        with self.assertRaises(ValueError):
            control_fixture(net.replace("MCM (PG RCG","MCM (PG PG"),True)

    def test_drawing_changes_only_named_connections_and_passives(self):
        sizes=dict(length_um=1.5,plate_um=27.5,branch_uA=1.2,input=2,load=4,driver=6,sink=3,
                   reference=1.35,tail=2.7,bias_ohm=900000,null_ohm=17000,cm_input=.45,cm_length_um=3.75,
                   paired_geometry=GEOMETRY,source_trim=BANKS["centered"])
        _,old=sources("process",sizes)
        _,new=sources("process",dict(sizes,**DESIGNS["r130"]))
        for cell in old:
            if cell not in (CMFB,OUTPUT):
                self.assertEqual(old[cell],new[cell])
        self.assertEqual(set(new[CMFB])-set(old[CMFB]),{"RREC"})
        for name in old[CMFB]:
            if name=="MCM":
                self.assertEqual(dict(old[CMFB][name]["terms"],G="RCG"),new[CMFB][name]["terms"])
                self.assertEqual(old[CMFB][name]["parameters"],new[CMFB][name]["parameters"])
            else:
                self.assertEqual(old[CMFB][name]["terms"],new[CMFB][name]["terms"])
                self.assertEqual(old[CMFB][name]["parameters"],new[CMFB][name]["parameters"])
        resistor=new[CMFB]["RREC"]
        self.assertEqual(resistor["terms"],{"PLUS":"RCG","MINUS":"PG"})
        self.assertEqual((resistor["parameters"]["segW"],int(resistor["parameters"]["r"])),("1u",130000))
        for name,item in old[OUTPUT].items():
            changed=new[OUTPUT][name]
            self.assertEqual(item["terms"],changed["terms"])
            expected=dict(item["parameters"])
            if name in ("CCP","CCN"):
                expected.update(w="26.5u",l="26.5u")
            self.assertEqual(changed["parameters"],expected)


if __name__=="__main__":
    unittest.main()
