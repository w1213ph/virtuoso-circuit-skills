"""Prepare the pinned, unmodified SKY130 1.8 V MOS models for this example."""
import argparse
import gzip
import hashlib
import http.client
import json
import os
import tempfile
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


HERE = Path(__file__).resolve().parent
LOCK = json.loads((HERE / "models.lock.json").read_text(encoding="utf-8"))
CORNERS = ("tt", "ss", "ff")
DEVICES = ("nfet_01v8", "pfet_01v8")
LIB = "mirror.lib.spice"


def checksum(data):
    return hashlib.sha256(data).hexdigest()


def model_library(root):
    """Retain upstream global option and parameter blocks. Only the two MOS families enter the deck."""
    common = []
    keep = False
    for line in (root / "models/all.spice").read_text(encoding="utf-8").splitlines():
        stripped = line.lstrip()
        if not stripped.startswith("+"):
            keep = stripped.lower().split()[:1] in ([".param"], [".option"], [".options"])
        if keep:
            common.append(line)
    if not common or not any("scale=" in l.lower() for l in common):
        raise ValueError("Missing upstream geometry scaling")
    lines = ["* SKY130 1.8 V MOS subset for the current mirror", "* Upstream revision " + LOCK["revision"]]
    for corner in CORNERS:
        lines.extend([".lib " + corner, *common, '.include "models/parameters/lod.spice"'])
        for device in DEVICES:
            stem = "cells/{}/sky130_fd_pr__{}__".format(device, device)
            lines.extend(['.include "{}{}.corner.spice"'.format(stem, section) for section in (corner, "mismatch")])
        lines.append(".endl " + corner)
    return ("\n".join(lines) + "\n").encode("utf-8")


def verify(root):
    for name, expected in LOCK["files"].items():
        if checksum((root / name).read_bytes()) != expected:
            raise ValueError("Model input differs from its pinned checksum: " + name)
    if (root / LIB).read_bytes() != model_library(root):
        raise ValueError("Local model library differs from its recipe")
    return root / LIB


def fetch(name):
    url = "https://raw.githubusercontent.com/{}/{}/{}".format(
        LOCK["repository"], LOCK["revision"], urllib.parse.quote(name, safe="/"))
    request = urllib.request.Request(url, headers={"User-Agent": "sky130-current-mirror", "Accept-Encoding": "gzip"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                content = response.read()
                return gzip.decompress(content) if response.getheader("Content-Encoding") == "gzip" else content
        except (OSError, http.client.HTTPException) as exc:
            if attempt == 2:
                raise ValueError("Could not fetch pinned model input " + name) from exc


def prepare(out, source=None):
    if out.exists():
        return verify(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=str(out.parent)) as temp:
        staged = Path(temp) / "models"
        staged.mkdir()

        def copy(name):
            data = (source / name).read_bytes() if source else fetch(name)
            if checksum(data) != LOCK["files"][name]:
                raise ValueError("Unverified model input: " + name)
            target = staged / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(copy, LOCK["files"]))
        (staged / LIB).write_bytes(model_library(staged))
        verify(staged)
        try:
            os.rename(str(staged), str(out))
        except OSError:
            if not out.is_dir():
                raise
        return verify(out)


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--out", type=Path, required=True, help="Dedicated directory for the pinned models")
    cli.add_argument("--from-dir", type=Path, help="Existing upstream source tree for an offline copy")
    args = cli.parse_args()
    try:
        print(prepare(args.out.expanduser().resolve(), args.from_dir.expanduser().resolve() if args.from_dir else None))
    except (OSError, ValueError, http.client.HTTPException) as exc:
        cli.exit(1, str(exc) + "\n")
