"""Entry point for push-triggered research runs: runs the current probe."""
import runpy
import sys

runpy.run_path("research/hourly_index.py", run_name="__main__")
sys.exit(0)
