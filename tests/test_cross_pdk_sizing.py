import sys
import unittest
from pathlib import Path

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
sys.path[:0] = [str(EXAMPLES / "cross-pdk-current-mirror"), str(EXAMPLES / "sky130-current-mirror")]

import mos_table
import native
import transfer_models
from mos_model import GF180, SKY130


class CrossPdkSizingTests(unittest.TestCase):
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
