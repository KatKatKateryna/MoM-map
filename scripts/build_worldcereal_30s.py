"""Moved to scripts/worldcereal/build_30s.py. This stub only keeps the WorldCereal run started
before the move working (its later stages call this path); delete it once that run is done."""
import runpy
import sys
from pathlib import Path

new = Path(__file__).resolve().parent / "worldcereal" / "build_30s.py"
sys.argv[0] = str(new)
sys.path.insert(0, str(new.parent))
runpy.run_path(str(new), run_name="__main__")
