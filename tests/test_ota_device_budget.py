import importlib.util
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest

CASE = Path(__file__).resolve().parents[1] / "examples/smic18-ota-sizing"
sys.path.insert(0, str(CASE))
# The comparator also has a module named characterize.
spec = importlib.util.spec_from_file_location("ota_device_budget", CASE / "characterize.py")
budget = importlib.util.module_from_spec(spec)
spec.loader.exec_module(budget)
import pair_views


class OtaDeviceBudgetTests(unittest.TestCase):
    def test_both_output_conductances_load_the_signal_transistor(self):
        gain = budget.common_source_db(dict(gm_id_V=16, gds_id_V=0.08), dict(gds_id_V=0.12))
        self.assertAlmostEqual(gain, 20 * math.log10(80))

    def test_reference_cards_require_the_actual_bulk_terminal_width_and_multiplicity(self):
        name = pair_views.cell_name(1)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            native = out / name
            native.mkdir()
            (native / "si.stdout").write_text("End netlisting benchmark\n", encoding="utf-8")
            instances, lines = [], []
            for kind, rail in (("N", "VSS"), ("P", "VDD")):
                terms = dict(D="D", G="G" + kind, S=rail, B=rail)
                instances.append(dict(name="M" + kind, cell=kind.lower() + "18", lib="pdk",
                                      terms=[dict(name=k, net=v) for k, v in terms.items()]))
                lines.append("M{0} (D G{0} {1} {1}) {2}18 w=(2u) l=1u nf=1 m=(1)".format(kind, rail, kind.lower()))
            ports = [dict(name=k, direction=v) for k, v in pair_views.PORTS.items()]
            (out / (name + "-schematic.json")).write_text(json.dumps(dict(instances=instances, ports=ports)), encoding="utf-8")
            path = native / "netlist"
            text = "\n".join(lines)
            path.write_text(text, encoding="utf-8")
            self.assertEqual(set(pair_views.read_cards(out, name, "pdk", 1)), {(1, "N"), (1, "P")})
            for bad in (text.replace("GP VDD VDD", "GP VDD VSS"), text.replace("l=1u", "l=1n"), text.replace("m=(1)", "m=(2)")):
                path.write_text(bad, encoding="utf-8")
                with self.assertRaises(ValueError):
                    pair_views.read_cards(out, name, "pdk", 1)
