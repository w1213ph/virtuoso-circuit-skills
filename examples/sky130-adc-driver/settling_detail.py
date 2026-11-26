"""Replay both acquisition directions on a generated SKY130 sampling-driver result."""
import argparse
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent/"sky130-current-mirror"),str(HERE.parents[1]/"skills/paper-to-virtuoso/scripts")]
from bench import requirements, transient_metrics
from mos_table import STATES


def review(root):
    spec = requirements(root/"requirements.json")
    version = json.loads((root/"recipe.json").read_text(encoding="utf8")).get("schema_version",1)
    if type(version) is not int or version not in (1,2,3,4):
        raise ValueError("Unknown acquisition measurement format")
    results = {}
    designs = {p.name for p in root.iterdir() if (p/"tt/measurement.json").is_file()}
    if not {"baseline","candidate"}<=designs:
        raise ValueError("The reference and candidate sampling drivers are required")
    for design in sorted(designs):
        for corner,_,vdd in STATES:
            folder = root/design/corner
            recorded = json.loads((folder/"measurement.json").read_text(encoding="utf8"))["transient"]
            current = transient_metrics(folder/"track",spec,vdd,settling_detail=True)
            windows = {edge:v.pop("settling_detail") for edge,v in current["edges"].items()}
            if version<4:
                for edge,values in current["edges"].items():
                    if "switch_drop_mV" not in recorded["edges"][edge]:
                        values.pop("switch_drop_mV")
            if current!=recorded:
                raise ValueError("A stored clock, sample or acquisition measurement disagrees with the waveform")
            results[design+"-"+corner] = windows
    return results


if __name__=="__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("result",type=Path)
    print(json.dumps(review(p.parse_args().result),indent=2,allow_nan=False))
