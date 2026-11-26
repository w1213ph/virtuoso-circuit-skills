"""Replay the downloaded ADC bias project, or simulate new requirements in a fresh directory."""
import sys
from pathlib import Path


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    sys.path.insert(0, str(root / "source/examples/cross-pdk-current-mirror"))
    import transfer

    args = sys.argv[1:] or ["verify", "--out", str(root / "evidence")]
    sys.exit(transfer.main(args, requirements=root / "my-bias.json", optimize=True))
