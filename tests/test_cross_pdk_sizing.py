import sys
import struct
import tempfile
import unittest
from pathlib import Path

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
sys.path[:0] = [str(EXAMPLES / "cross-pdk-current-mirror"), str(EXAMPLES / "sky130-current-mirror")]

import mos_table
import native
import preview
import project
import sizing_search
import transfer_models
from mos_model import GF180, SKY130


class CrossPdkSizingTests(unittest.TestCase):
    def test_preview_inventory_binds_each_png_to_the_measured_run_and_editable_view(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            images = root / "images"
            images.mkdir()
            index = root / "evidence.json"
            index.write_bytes(b"first run")
            report = {"processes": {"sky130": {"selected": "short", "diagnostic": None}}}
            for _, view, name in preview.views("sky130", report["processes"]["sky130"]):
                (root / view).parent.mkdir(parents=True, exist_ok=True)
                (root / view).write_bytes(b"native view")
                (images / name).write_bytes(b"\x89PNG\r\n\x1a\n" + struct.pack(">I4sII", 13, b"IHDR", 1200, 800))
            original = preview.inventory(root, images, report)
            index.write_bytes(b"second run")
            self.assertNotEqual(original["evidence_sha256"], preview.inventory(root, images, report)["evidence_sha256"])
            (root / view).write_bytes(b"different native view")
            self.assertNotEqual(original["images"][name]["view_sha256"],
                                preview.inventory(root, images, report)["images"][name]["view_sha256"])
            with (images / name).open("ab") as f:
                f.write(b"changed pixels")
            self.assertNotEqual(original["images"][name]["png_sha256"],
                                preview.inventory(root, images, report)["images"][name]["png_sha256"])

    def test_excluded_widths_report_no_measured_design_and_no_image_links(self):
        data = dict(selected=None, diagnostic=None)
        self.assertEqual(list(preview.views("gf180", data)), [])
        section = project.process_section("gf180", data, {})
        self.assertIn("没有可仿真的候选", section)
        self.assertNotIn("<img", section)

    def test_duplicate_grid_points_share_one_design_and_keep_the_lookup_row(self):
        rows = [dict(polarity="N", length_um=l, vds_V=0.65, vbs_V=0, gm_id_per_V=14.8,
                     abs_id_uA=8, id_per_width_uA_um=4) for _,l in sizing_search.LENGTHS]
        designs, trials = sizing_search.candidates(rows, "sky130", 10)
        self.assertEqual(set(designs), {"short", "long"})
        self.assertEqual(len(trials), 12)
        self.assertEqual(trials["short-g10"]["design"], "short")
        self.assertEqual(trials["long-g22"]["status"], "same_geometry")
        self.assertEqual(designs["short"]["selected_row"], rows[0])
        self.assertEqual(designs["long"]["mirror_pair_gate_area_um2"], 5)
        for current in (0.001, 1000):
            excluded, traces = sizing_search.candidates(rows, "gf180", current)
            self.assertFalse(excluded)
            self.assertEqual({r["status"] for r in traces.values()}, {"outside_width_range"})

    def test_incomplete_settling_points_to_the_measured_lower_bound_and_dc_failure(self):
        case = dict(sizing=dict(width_um=2, length_um=0.5, mirror_pair_gate_area_um2=2),
                    corners={"tt":dict(metrics=dict(worst_error_pct=10, worst_vout_V=1.2, settling_ns=25)),
                             "ss":dict(metrics=dict(worst_error_pct=3, worst_vout_V=0.4, settling_ns=None,
                                                    settling_lower_bound_ns=289.01))})
        line = sizing_search.case_line("sky130", "short", case, dict(max_error_pct=8, max_settling_ns=40))
        for part in ("tt/dc.dat", "ss/transient.dat", "&gt;289.01 ns", "直流误差超限", "观察结束仍未建立"):
            self.assertIn(part, line)

    def test_diagnostic_prefers_the_fastest_dc_qualified_or_least_dc_excess(self):
        def sample(error, time, area):
            return dict(worst_error_pct=error, worst_settling_ns=time, sizing=dict(mirror_pair_gate_area_um2=area))
        limits = dict(max_error_pct=8)
        cases = {"a":sample(9, 20, 2), "b":sample(7, None, 4), "c":sample(8, 45, 8), "d":sample(6, 50, 3)}
        self.assertEqual(sizing_search.diagnostic_candidate(cases, limits), "c")
        self.assertEqual(sizing_search.diagnostic_candidate({"a":cases["a"], "e":sample(9.5, 10, 1)}, limits), "a")
        self.assertIsNone(sizing_search.diagnostic_candidate({}, limits))

    def test_four_terminal_instances_use_the_units_of_each_upstream_model(self):
        for model, card, vector in ((GF180, "nmos_3p3 w=2u l=0.5u nf=1", "@m.xn000.m0[gm]"),
                                    (SKY130, "sky130_fd_pr__nfet_01v8 w=2 l=0.5 nf=1",
                                     "@m.xn000.msky130_fd_pr__nfet_01v8[gm]")):
            deck, curves = mos_table.scans("mos.lib", "tt", 27, 1.8, model=model)
            self.assertEqual(len(curves), 24)
            self.assertIn(card, deck)
            self.assertIn(vector, deck)
        self.assertEqual(GF180.geometry("as", 0.84), "0.84p")
        self.assertEqual(GF180.geometry("pd", 6.2), "6.2u")

    def test_native_unit_loss_even_with_correct_numerals_is_rejected(self):
        width, length = 6.1, 0.5
        params = "w=6.1u l=0.5u nf=1 ad=1.83p as=1.83p pd=12.8u ps=12.8u"
        deck = "\n".join(["vbias vdd 0 dc 'vddtest'", "irefdrv vdd iref dc 'iset' pulse(0 'iset' 10n 1n 1n 1u 2u)",
                         "cref iref 0 250f m=1", "xbias iref iout 0 bias_mirror", "vforce iout 0 dc 'vouttest'",
                         ".subckt bias_mirror iref iout vss", "xref iref iref vss vss nmos_3p3 " + params,
                         "xmir iout iref vss vss nmos_3p3 " + params, ".ends", ".end"])
        data, _ = native.audit(deck, width, length, 250, 40, GF180)
        self.assertEqual(data["device_connections"]["xmir"]["D"], "iout")
        for broken in (deck.replace("6.1u", "6.1"), deck.replace("1.83p", "1.83u"),
                       deck.replace("nmos_3p3", SKY130.nmos), deck.replace("xmir iout iref", "xmir iref iout")):
            with self.assertRaises(ValueError):
                native.audit(broken, width, length, 250, 40, GF180)

    def test_all_deterministic_corners_override_the_upstream_statistics_switches(self):
        sections = transfer_models.wrapper().decode().splitlines()
        for name, upstream in (("tt", "typical"), ("ss", "ss"), ("ff", "ff")):
            start, end = sections.index(".lib " + name), sections.index(".endl " + name)
            self.assertEqual(sections[start+1:end], ['.include "models/ngspice/design.ngspice"',
                             ".param sw_stat_global=0 sw_stat_mismatch=0",
                             '.lib "models/ngspice/sm141064.ngspice" ' + upstream])
