"""Export the actual Xschem views linked by a verified sizing report to PNG."""
import argparse
import os
from pathlib import Path
import struct
import subprocess

import transfer
import native


def render(out, images):
    if out == images or out in images.parents:
        raise ValueError("Choose a separate directory for the previews so the waveform archive stays replayable")
    report = transfer.verify(out)
    images.mkdir(parents=True, exist_ok=False)
    command = "xschem zoom_full; xschem print png $::env(MIRROR_PREVIEW) 1200 800; exit"
    for process, data in report["processes"].items():
        selected = data["selected"]
        name = selected or data.get("diagnostic")
        if not name:
            continue
        views = out / process / name / "views"
        label = "qualified" if selected else "over-limit"
        for kind, cell in (("core", "BIAS_MIRROR.sch"), ("bench", "TB_BIAS_MIRROR.sch"), ("symbol", "BIAS_MIRROR.sym")):
            png = images / "{}-{}-{}-{}.png".format(process, name, label, kind)
            env = dict(os.environ, MIRROR_SCHEMATIC_DIR=str(views), MIRROR_PREVIEW=str(png))
            subprocess.run([os.environ.get("XSCHEM", "xschem"), "--tcl", native.TCL_PATH + "; set draw_grid 0",
                            "--command", command, str(views / cell)], cwd=str(views), env=env, timeout=60, check=True)
            with png.open("rb") as f:
                header = f.read(24)
            if (header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR"
                    or struct.unpack(">II", header[16:24]) != (1200, 800)):
                raise ValueError("Unexpected image from the native editor: " + str(png))
            print(png.name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="Existing raw evidence directory")
    parser.add_argument("--images", type=Path, required=True, help="New and separate PNG directory")
    args = parser.parse_args()
    try:
        render(args.out.resolve(), args.images.resolve())
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        parser.exit(2, str(exc) + "\n")
