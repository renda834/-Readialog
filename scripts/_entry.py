"""Common bootstrap for commands run directly from the scripts directory."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wren.utilities import run_utility


def run() -> None:
    raise SystemExit(run_utility(Path(sys.argv[0]).stem))
