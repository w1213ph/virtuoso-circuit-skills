"""Check the gain receiver node, physical control pins and last-excursion settling rule."""
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

CASES=Path(__file__).resolve().parents[1]/"examples"
CASE=CASES/"sky130-programmable-gain"
sys.path.insert(0,str(CASES/"sky130-current-mirror"))
sys.path.insert(0,str(CASE))
import gain_measure as gain
from gain_native import parent_leads


class ProgrammableGain(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder=Path(tmp.name)
        self.r=gain.requirements(CASE/"requirements.example.json")

    def op(self,name,vin,b0,b1):
        values=[1.8,vin,.70,.7,.69,.68,.08,b0,b1,1.8-b0,1.8-b1,-20e-6]
        (self.folder/name).write_text("scale "+" ".join(gain.OP)+"\n0 "+" ".join(map(str,values))+"\n")

    def test_internal_gain_cannot_cover_the_loaded_gate_or_swapped_controls(self):
        self.op("op.dat",.72,0.,1.8)
        self.op("offset.dat",.728,0.,1.8)
        path=self.folder/"ac.dat"
        lines=["frequency "+" ".join(gain.AC)]
        for i in range(gain.GRID):
            hz=10000+i*(1e6-10000)/(gain.GRID-1)
            lines.append("{} 1 0 -3 0 -2 0".format(hz))
        path.write_text("\n".join(lines)+"\n")
        measured=gain.static_metrics(self.folder,self.r,"10",1.8)
        self.assertAlmostEqual(measured["drive_high_dB"],9.542425,places=5)
        self.assertGreater(measured["max_gain_error_dB"],3.5)
        with self.assertRaisesRegex(ValueError,"controls"):
            gain.static_metrics(self.folder,self.r,"01",1.8)
        path.write_text("\n".join(lines[:-1])+"\n")
        with self.assertRaisesRegex(ValueError,"Incomplete gain sweep"):
            gain.static_metrics(self.folder,self.r,"10",1.8)

    def test_ringing_counts_until_the_last_excursion(self):
        path=self.folder/"transient.dat"
        lines=["time "+" ".join(gain.TRANSIENT)]
        for ns in range(0,7001,2):
            high=1002<=ns<=4000
            level=1.8 if high else 0.
            rx=.66 if high else .7
            if 1200<=ns<=1250:
                rx+=.004
            lines.append("{:.12g} .728 .7 {} {} {}".format(ns*1e-9,rx,level,level))
        path.write_text("\n".join(lines)+"\n")
        result=gain.switching(self.folder,self.r,1.8,{"00":.70,"11":.66})
        self.assertAlmostEqual(result["11"]["settling_ns"],251,places=5)
        self.assertLess(result["00"]["settling_ns"],3)

    def test_parent_bottom_pin_must_lead_outward(self):
        for p in list(CASE.glob("*.sym"))+list(CASE.glob("*.sch")):
            shutil.copyfile(p,self.folder/p.name)
        self.assertEqual(len(parent_leads(self.folder)),20)
        p=self.folder/"TB_PGA_GAIN.sch"
        text=p.read_text().replace("310 330 310 424","310 330 310 320")
        p.write_text(text)
        with self.assertRaisesRegex(ValueError,r"XPGA\.B1"):
            parent_leads(self.folder)
