"""Entry point for push-triggered research runs.

Paper-week pull: market list + 1-min prices (no IEM), trades, Kalshi index.
"""
import runpy
import sys

g = runpy.run_path("research/hourly_history.py", run_name="lib")
g["kalshi"]()
runpy.run_path("research/hourly_trades.py", run_name="__main__")
runpy.run_path("research/hourly_index.py", run_name="__main__")
sys.exit(0)
