"""Entry point for the sports-research workflow; runs the current stage."""
import runpy
import sys
from pathlib import Path

runpy.run_path(str(Path(__file__).parent / "discover.py"), run_name="__main__")
_ = sys
