"""Regression checks for sign, settling, evidence truncation and cross-corner power selection."""
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest


CASE = Path(__file__).resolve().parents[1]/"examples/smic18-ota-sizing"


class LoadedOtaTests(unittest.TestCase):
    def evaluate(self, program):
        # Native examples have local modules with overlapping names. Use the public case's import context.
        prefix = "import sys\nfrom pathlib import Path\nsys.path.insert(0, " + repr(str(CASE)) + ")\nimport ota, ota_measure as m\n"
        subprocess.run([sys.executable,"-B","-c",prefix+textwrap.dedent(program)], check=True)

    def test_choose_passing_geometry_by_worst_corner_then_area(self):
        self.evaluate("""
            def case(tt,ff,area,passed=True):
                return dict(corners={"tt":dict(power_uW=tt,passed=True),"ff":dict(power_uW=ff,passed=passed)},
                            physical={"area":{"mos_gate_um2":area}})
            cases={"tiny":case(2,3,1,False),"fast":case(5,22,10),"cool":case(9,14,15),"small":case(10,14,9)}
            assert ota.first_passing(cases)=="small"
            assert ota.first_passing({"tiny":cases["tiny"]}) is None
        """)

    def test_phase_wrap_and_multiple_unity_crossings(self):
        self.evaluate("""
            import cmath,math
            x=[1,10,100,1000]
            polar=lambda magnitude,degrees: cmath.rect(magnitude,math.radians(degrees))
            result=m.unity(x,[polar(10,-2),polar(2,-105),polar(.5,-135),polar(.1,175)])
            assert math.isclose(result["phase_margin_deg"],60)
            assert math.isclose(result["crossings"][0]["frequency_Hz"],math.sqrt(1000))
            assert m.unity(x,[10,.5,2,.1])["phase_margin_deg"] is None
            assert m.unity(x,[-10,-2,-.5,-.1])["phase_margin_deg"] is None
        """)

    def test_settling_waits_for_last_excursion_and_detects_failure(self):
        self.evaluate("""
            t=[i*1e-7 for i in range(10)]
            v=[0,0,1.01,.95,.993,1.0,1.0,1.0,1.0,1.0]
            assert abs(m.settling(t,v,1,.02,1e-7,9e-7)-.3)<1e-10
            v[-1]=.9
            assert m.settling(t,v,1,.02,1e-7,9e-7) is None
        """)

    def test_psf_rejects_missing_middle_point_or_incomplete_signal(self):
        self.evaluate(r'''
            import tempfile
            with tempfile.TemporaryDirectory() as tmp:
                p=Path(tmp)/"gain.ac"
                lines=['"freq" %.12e\n"OUT" (1 -2)'%(10**(i/60)) for i in range(541)]
                for damaged in (None,270,"signal"):
                    points=lines.copy()
                    if damaged==270:points.pop(270)
                    if damaged=="signal":points[270]=points[270].splitlines()[0]
                    p.write_text("HEADER\nVALUE\n"+"\n".join(points)+"\nEND\n",encoding="utf-8")
                    if damaged is None:
                        assert len(m.samples(p,"freq",("OUT",))["freq"])==541
                    else:
                        try:m.samples(p,"freq",("OUT",))
                        except ValueError:pass
                        else:raise AssertionError("Missing electrical evidence accepted")
        ''')
