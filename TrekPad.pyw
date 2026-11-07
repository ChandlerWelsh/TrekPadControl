"""Double-click to start TrekPad Control (runs without a console window)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from trekpad.main import main  # noqa: E402

main()
