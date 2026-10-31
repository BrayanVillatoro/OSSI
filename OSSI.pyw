"""Double-click this file to open OSSI. It needs no install and no terminal."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from ossi.gui import main

raise SystemExit(main(background="--background" in sys.argv))
