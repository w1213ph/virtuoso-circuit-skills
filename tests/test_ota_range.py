"""The measured static range must stay connected to the intended operating point."""
from pathlib import Path
import sys
import unittest

CASE = Path(__file__).resolve().parents[1]/"examples/smic18-ota-sizing"
sys.path.insert(0,str(CASE))
import ota_range


class StaticRange(unittest.TestCase):
    def test_hole_breaks_range_and_both_neighbours_are_preserved(self):
        rows = [dict(volts=i/10,failed=["gain"] if i in (2,5) else []) for i in range(8)]
        report = ota_range.contiguous(rows,4,"volts")
        self.assertEqual(report["bounds_V"],[.3,.4])
        self.assertEqual([v["volts"] for v in report["adjacent_failures"]],[.2,.5])
        self.assertEqual(report["other_passing_points"],4)
        self.assertFalse(ota_range.covers(report["bounds_V"],[.1,.4]))
        self.assertIsNone(ota_range.contiguous(rows,2,"volts")["bounds_V"])

    def test_fixture_change_is_limited_to_one_named_native_source(self):
        native = "subckt DUT OUT VSS\nM0 (OUT G S VSS) n18 w=1u\nends DUT\nVD (CMD 0) vsource dc=0 type=pulse\nVD2 (X 0) vsource dc=0\n"
        changed = ota_range.replace_fixture_field(native,"VD","dc","0","1e-7")
        self.assertEqual(changed.replace("dc=1e-7","dc=0"),native)
        self.assertEqual(changed.count("dc=1e-7"),1)
        with self.assertRaises(ValueError):
            ota_range.replace_fixture_field(native+native,"VD","dc","0","1e-7")

    def test_output_common_mode_can_fail_with_accurate_differential_tracking(self):
        p,n = .87146+.24749/2,.87146-.24749/2
        row = dict(output_p_V=p,output_n_V=n,output_cm_V=(p+n)/2,headroom_V=.23,power_uW=24)
        self.assertLess(abs((p-n)-.2475),.001)
        checks = ota_range.limits_at(row,dict(row),1.8)
        self.assertEqual([k for k,v in checks.items() if not v],["output_common_mode"])
