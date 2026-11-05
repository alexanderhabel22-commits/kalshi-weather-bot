Offline analysis of the econ-research-data branch (Nov 5, 2026). Scripts expect the run
files unpacked under /tmp/claude-0/econ (markets.csv, candles.csv.gz, cleveland_month.json),
/tmp/claude-0/econ3/trades.csv.gz and /tmp/claude-0/gdp (GDPNow workbook + gdpnow.csv).
infl.py = CPI/PCE vs Cleveland nowcast; gdp.py = GDP vs GDPNow; calib.py = taker P&L by price band;
maker.py = maker P&L from trades; pay.py / wr.py = per-series band checks; pit.py = calibration of the implied distribution.
Results: Claude Doc "Kalshi economic data markets: edge check".
