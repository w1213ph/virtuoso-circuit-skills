"""Verify the delivered sampling driver, or run the bundled designer on a new specification."""
import argparse
import sys
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent
    sys.path.insert(0, str(root / "source/examples/sky130-adc-driver"))
    import run as designer
    from verify_results import verify

    args = sys.argv[1:]
    if args[:1] == ["run"]:
        return designer.main(args[1:], default_requirements=root / "my-driver.json")
    parser = argparse.ArgumentParser(description=__doc__, epilog="For a new design use: python sample.py run --out my-design")
    parser.add_argument("action", nargs="?", choices=["verify"], default="verify")
    parser.add_argument("--out", type=Path, default=root / "evidence", help="Driver measurement directory")
    opts = parser.parse_args(args)
    try:
        result = verify(opts.out.resolve(), detailed=True)
        from driver_review import limiting_measurements
        print("Half-LSB limit: {:g} mV | device points: {}".format(
            result["budget"]["half_lsb_mV"], sum(result["characterization_points"].values())))
        for name, variant in result["variants"].items():
            track, hold, margin = limiting_measurements(variant["measurements"])
            print("{}: {}/18 | core {:.3f} + switch {:.3f} um^2 | track {:.4f} mV ({} {}) | hold {:.4f} mV ({} {}) | PM {:.2f} deg ({} {})".format(
                name, result["pass_counts"][name], variant["sizing"]["core_gate_area_um2"],variant["sizing"]["switch_gate_area_um2"],
                track[2], track[0], track[1], hold[2], hold[0], hold[1], margin[2], margin[0], margin[1]))
        print("Selected:", result["selected"] or "none; every measured version exceeds a limit")
        return 0 if result["selected"] else 1
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        parser.exit(2, str(exc) + "\n")


if __name__ == "__main__":
    sys.exit(main())
