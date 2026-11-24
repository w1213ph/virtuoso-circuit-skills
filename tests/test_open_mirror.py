import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

CASE = Path(__file__).resolve().parents[1] / "examples/sky130-current-mirror"
sys.path.insert(0, str(CASE))
import fetch_models
import measure
import mos_table
import native


class OpenMirrorTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_pinned_model_copy_checks_payload_and_existing_directory(self):
        source = self.root / "source"
        (source / "models").mkdir(parents=True)
        content = b"* reference\n.option scale=1.0u\n.param\n+ nominal=1\n"
        (source / "models/all.spice").write_bytes(content)
        target = self.root / "installed"
        with patch.dict(fetch_models.LOCK, files={"models/all.spice":fetch_models.checksum(content)}):
            library = fetch_models.prepare(target, source)
            self.assertIn("+ nominal=1", library.read_text())
            self.assertEqual(fetch_models.prepare(target, source), library)
            (target / "models/all.spice").write_bytes(b"modified")
            with self.assertRaisesRegex(ValueError, "checksum"):
                fetch_models.prepare(target, source)
            self.assertEqual((target / "models/all.spice").read_bytes(), b"modified")
        reply = MagicMock()
        reply.__enter__.return_value = reply
        reply.read.return_value = content
        reply.getheader.return_value = None
        with patch.dict(fetch_models.LOCK, files={"models/all.spice":fetch_models.checksum(content)}), \
                patch.object(fetch_models.urllib.request, "urlopen", side_effect=[OSError("unavailable"), reply]) as fetch:
            self.assertEqual(fetch_models.fetch("models/all.spice"), content)
        fallback = fetch.call_args_list[1][0][0]
        self.assertEqual(fallback.host, "api.github.com")
        self.assertIn("ref="+fetch_models.LOCK["revision"], fallback.full_url)
        partial = MagicMock()
        partial.__enter__.return_value = partial
        partial.getheader.side_effect = {"Content-Encoding":None, "Content-Length":"4"}.get
        partial.read.side_effect = fetch_models.http.client.IncompleteRead(b"ab", 2)
        tail = MagicMock()
        tail.__enter__.return_value = tail
        tail.getheader.return_value = "bytes 2-3/4"
        tail.status, tail.read.return_value = 206, b"cd"
        with patch.object(fetch_models.urllib.request, "urlopen", side_effect=[partial,tail]) as fetch:
            self.assertEqual(fetch_models.download(fallback), b"abcd")
            self.assertEqual(fetch.call_args_list[1][0][0].get_header("Range"), "bytes=2-3")
        tail.status = 200
        with patch.object(fetch_models.urllib.request, "urlopen", side_effect=[partial,tail]):
            with self.assertRaisesRegex(ValueError, "requested byte range"):
                fetch_models.download(fallback)

    def test_parent_lead_audit_rejects_an_inward_connection_on_the_right(self):
        for name in ("BIAS_MIRROR.sym", "TB_BIAS_MIRROR.sch"):
            shutil.copyfile(CASE / name, self.root / name)
        result = native.leads(self.root)
        self.assertEqual(result["VSS"]["outward_vector"], (0,1))
        parent = self.root / "TB_BIAS_MIRROR.sch"
        parent.write_text(parent.read_text().replace("N 520 250 650 250", "N 520 250 510 250"))
        with self.assertRaisesRegex(ValueError, "IOUT"):
            native.leads(self.root)

    def test_empty_netlist_and_missing_device_cannot_pass(self):
        with self.assertRaisesRegex(ValueError, "terminal order"):
            native.audit("** empty schematic\n.end\n", 3.45, 0.5)
        with self.assertRaisesRegex(ValueError, "testbench"):
            native.audit(".subckt BIAS_MIRROR IREF IOUT VSS\n.ends\n.end\n", 3.45, 0.5)

    def test_lookup_selects_current_density_of_the_requested_geometry(self):
        row = {"polarity":"N", "length_um":0.5, "vds_V":0.65, "vbs_V":0,
               "gm_id_per_V":14.95, "abs_id_uA":5.25, "id_per_width_uA_um":2.625}
        groups = [dict(row, polarity="P", gm_id_per_V=15), dict(row, vbs_V=-0.15, gm_id_per_V=15), row]
        sizing = mos_table.choose(groups, 0.5)
        self.assertAlmostEqual(sizing["width_um"], 3.8)
        self.assertEqual(sizing["selected_row"], row)
        half = mos_table.choose(groups, 0.5, current_uA=5)
        self.assertAlmostEqual(half["width_um"], 1.9)
        self.assertEqual(half["reference_current_uA"], 5)
        standard, _ = mos_table.scans("pdk", "tt", 27, 1.8)
        extended, _ = mos_table.scans("pdk", "tt", 27, 1.8, (0.5,1.5))
        self.assertNotIn("l=1.5 nf=1", standard)
        self.assertIn("l=1.5 nf=1", extended)
        with self.assertRaisesRegex(ValueError, "No applicable"):
            mos_table.choose(groups, 1)

    def test_cache_invalidates_changed_data_and_changed_simulator(self):
        names = ("input.spice", "table.csv")
        for name in names:
            (self.root / name).write_bytes(name.encode())
        recipe = {"simulator":"test", "input_sha256":fetch_models.checksum(b"input.spice")}
        report = dict(recipe=recipe, files={n:fetch_models.checksum((self.root / n).read_bytes()) for n in names})
        mos_table.save_json(self.root / "manifest.json", report)
        self.assertEqual(mos_table.cache_record(self.root, recipe, names), report)
        (self.root / "input.spice").write_bytes(b"wrong input")
        report["files"]["input.spice"] = fetch_models.checksum(b"wrong input")
        mos_table.save_json(self.root / "manifest.json", report)
        self.assertIsNone(mos_table.cache_record(self.root, recipe, names))
        (self.root / "input.spice").write_bytes(b"input.spice")
        report["files"]["input.spice"] = recipe["input_sha256"]
        mos_table.save_json(self.root / "manifest.json", report)
        self.assertIsNone(mos_table.cache_record(self.root, {"simulator":"new"}, names))
        (self.root / "table.csv").write_text("changed")
        self.assertIsNone(mos_table.cache_record(self.root, recipe, names))

    def test_measurement_uses_actual_reference_and_rejects_a_truncated_pulse(self):
        header = "scale v(iout) v(iref) i(vforce) i(vbias) v(vdd)\n"
        dc = []
        for i in range(151):
            v = i / 100
            current = 1e-5 * min(v/0.25, 1) * (1 + 0.02*(v-0.8))
            dc.append("{} {} 0.7 {} -0.00001 1.8\n".format(v,v,-current))
        (self.root / "dc.dat").write_text(header + "".join(dc))
        tran = []
        for i in range(1501):
            time_ns = i * 0.2
            ref = 1e-5 * min(1,max(0,time_ns-10))
            out = 1e-5 * min(1,max(0,(time_ns-10)/10))
            tran.append("{:.12e} 0.8 0.7 {:.12e} {:.12e} 1.8\n".format(time_ns*1e-9,-out,-ref))
        raw = self.root / "transient.dat"
        raw.write_text(header + "".join(tran))
        result = measure.metrics(self.root, 1.8)
        self.assertIn("ISET=5u", measure.deck("library.spice", ("tt",27,1.8), "mirror\n.end\n", 5))
        with self.assertRaisesRegex(ValueError, "reference current"):
            measure.metrics(self.root, 1.8, reference_uA=5)
        self.assertIsNotNone(result["compliance_mV"])
        self.assertIsNone(measure.metrics(self.root, 1.8, max_error_pct=0.5)["compliance_mV"])
        self.assertAlmostEqual(result["worst_error_pct"], 0.8)
        self.assertGreater(result["settling_ns"], 8)
        self.assertLess(result["settling_ns"], 10)
        raw.write_text(header + "".join(tran[:500]))
        with self.assertRaisesRegex(ValueError, "Incomplete transient"):
            measure.metrics(self.root, 1.8)

    def test_invalid_design_requirements_stop_before_tool_lookup_or_output(self):
        for name, value in (("--reference-ua", "nan"), ("--max-error-pct", "-1")):
            with self.subTest(name=name):
                out = self.root / "run"
                result = subprocess.run([sys.executable, str(CASE / "run.py"), "--out", str(out), name, value],
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                self.assertEqual(result.returncode, 2)
                self.assertIn(b"finite positive", result.stderr)
                self.assertFalse(out.exists())
