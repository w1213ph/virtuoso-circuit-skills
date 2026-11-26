"""Create and read back complementary SMIC18 reference pairs in an isolated OA library."""
from pathlib import Path
import math
import re

from audit_native import spice_number
from schematic import draw_wire, note, pin, place, q, view_procedure
from spectre_run import execute, read_json


LIB = "FDOTA18_DEVICE_TB"
LENGTHS = (0.36, 0.60, 1.00, 1.50)
PORTS = {"GN": "input", "GP": "input", "D": "inputOutput", "VDD": "inputOutput", "VSS": "inputOutput"}
SHARED = Path(__file__).resolve().parents[1] / "dynamic-comparator"


def cell_name(length):
    return "TB_FDOTA18_PAIR_L{:03d}".format(round(100 * length))


def schematic(pdk, length):
    ops, instances = [], {}
    for name, master, y, rail, gate in (("MN", "n18", 2.5, "VSS", "GN"), ("MP", "p18", 5.5, "VDD", "GP")):
        place(ops, instances, name, pdk, master, (4, y), "R0", {"D": "D", "G": gate, "S": rail, "B": rail},
              {"l": "{:g}n".format(length * 1000), "fw": "2u", "fingers": "1", "m": "1"}, use_cdf=True)
    for name, xy, orient in (("GN", (1.25, 2.5), "R0"), ("GP", (1.25, 5.5), "R0"), ("D", (7, 4), "R0"),
                             ("VDD", (4.25, 7), "R90"), ("VSS", (4.25, 1), "R270")):
        ops.append(pin(name, xy, orient, PORTS))
    for points in ([(1.25, 2.5), (4, 2.5)], [(1.25, 5.5), (4, 5.5)],
                   [(4.25, 2.6875), (4.25, 4), (4.25, 5.3125)], [(4.25, 4), (7, 4)],
                   [(4.25, 1), (4.25, 1.75), (4.25, 2.3125)], [(4.25, 2.5), (5.25, 2.5), (5.25, 1.75), (4.25, 1.75)],
                   [(4.25, 5.6875), (4.25, 6.25), (4.25, 7)], [(4.25, 5.5), (5.25, 5.5), (5.25, 6.25), (4.25, 6.25)]):
        ops.append(draw_wire(points))
    ops += [note(0.875, 7.75, "Complementary MOS reference pair", 0.16),
            note(1.25, 5.78, "PMOS gate sweep", 0.10), note(1.25, 2.78, "NMOS gate sweep", 0.10),
            note(5.00, 4.20, "Drain bias", 0.10),
            note(0.875, 0.25, "Each device: W = 2 um, L = {:g} um. Separate gate biases.".format(length), 0.105)]
    return ops


def build_script(pdk):
    lines = [(SHARED / "native_helpers.il").read_text(encoding="utf-8")]
    for i, length in enumerate(LENGTHS):
        lines.append(view_procedure("pair%d" % i, cell_name(length), "schematic", schematic(pdk, length), LIB))
    calls = " ".join("pair%d()" % i for i in range(len(LENGTHS)))
    lines.append(f"""procedure(fdBuild() let((lib dest)
      cmpCheckPDK({q(pdk)})
      dest=strcat(getWorkingDir() "/{LIB}")
      when(ddGetObj("{LIB}") || isDir(dest) error("Device library already exists"))
      lib=ddCreateLib("{LIB}" dest)
      unless(lib && equal(lib~>readPath dest) error("Cannot create isolated library"))
      techBindTechFile(lib {q(pdk)})
      {calls}
      printf("FD_DEVICE_OK\\n") t))
      unless(errset(fdBuild() t) exit(3)) exit(0)""")
    return "\n".join(lines) + "\n"


def read_cards(folder, name, pdk, length):
    view = read_json(folder / (name + "-schematic.json"))
    if {p["name"]: p["direction"] for p in view["ports"]} != PORTS:
        raise ValueError("Native reference ports differ: " + name)
    devices = {inst["name"]: inst for inst in view["instances"] if inst["lib"] == pdk}
    if set(devices) != {"MN", "MP"}:
        raise ValueError("Expected one NMOS and one PMOS: " + name)
    netdir = folder / name
    if "End netlisting " not in (netdir / "si.stdout").read_text(encoding="utf-8", errors="replace"):
        raise ValueError("Incomplete SI netlist: " + name)
    net = re.sub(r"\\\r?\n\s*", " ", (netdir / "netlist").read_text(encoding="utf-8"))
    cards = {}
    for kind in ("N", "P"):
        rail = "VDD" if kind == "P" else "VSS"
        terms = {"D": "D", "G": "G" + kind, "S": rail, "B": rail}
        inst = devices["M" + kind]
        if inst["cell"] != kind.lower() + "18" or {t["name"]: t["net"] for t in inst["terms"]} != terms:
            raise ValueError("Incorrect native MOS master or terminal: " + name)
        matches = re.findall(r"(?m)^\s*M" + kind + r"\s+\(([^)\n]+)\)\s+([^\n]+)", net)
        if len(matches) != 1 or matches[0][0].split() != [terms[t] for t in ("D", "G", "S", "B")]:
            raise ValueError("SI instance terminals differ from the saved OA: " + name)
        card = matches[0][1].strip()
        params = dict(re.findall(r"(\w+)=\(?([^\s()]+)\)?", card))
        if (card.split()[0] != inst["cell"] or not math.isclose(spice_number(params["w"]), 2e-6)
                or not math.isclose(spice_number(params["l"]), length * 1e-6) or params["nf"] != "1" or params["m"] != "1"):
            raise ValueError("SI device dimensions, master or multiplicity differ: " + name)
        cards[length, kind] = card
    return cards


def prepare(folder, site, fresh):
    script = build_script(site["pdk_library"])
    if fresh:
        folder.mkdir()
        (folder / "build.il").write_text(script, encoding="utf-8")
        (folder / "cds.lib").write_text("INCLUDE " + q(str(site["cds_lib"])) + "\n", encoding="utf-8")
        (folder / ".cdsinit").write_text("; Isolated device study\n", encoding="utf-8")
        execute([site["virtuoso"], "-nograph", "-log", folder / "build.log", "-restore", folder / "build.il"],
                folder, "stdout", 240)
    log = (folder / "build.log").read_text(encoding="utf-8", errors="replace")
    if (folder / "build.il").read_text(encoding="utf-8") != script or "FD_DEVICE_OK" not in log:
        raise ValueError("Reference pair drawing or build log differs")
    cards = {}
    for length in LENGTHS:
        name = cell_name(length)
        if re.findall(r"CMP_CHECK " + name + r" (\([^\n]*\))", log) != ["(0 0)"]:
            raise ValueError("Reference pair did not pass Check & Save: " + name)
        netdir = folder / name
        if fresh:
            netdir.mkdir()
            (netdir / ".cdsinit").write_text("; Isolated SI export\n", encoding="utf-8")
            (netdir / "si.env").write_text(
                f'simLibName="{LIB}"\nsimCellName="{name}"\nsimViewName="schematic"\nsimSimulator="spectre"\n'
                'simNotIncremental=nil\nsimReNetlistAll=t\nsimViewList=list("spectre" "veriloga" "schematic")\n'
                'simStopList=list("spectre" "veriloga")\nsimNetlistHier=t\n', encoding="utf-8")
            execute([site["si"], "-batch", "-cdslib", folder / "cds.lib", "-command", "nl"], netdir, "si.stdout", 180)
        cards.update(read_cards(folder, name, site["pdk_library"], length))
    return cards
