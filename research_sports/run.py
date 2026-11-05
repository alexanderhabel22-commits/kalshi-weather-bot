"""Entry point for the sports-research workflow; runs the current stage."""
import runpy
from pathlib import Path

STAGE = "stage_b.py"
runpy.run_path(str(Path(__file__).parent / STAGE), run_name="__main__")
