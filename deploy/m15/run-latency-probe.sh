#!/usr/bin/env bash
# One-time order speed test (wxbot/latency_probe.py). update.sh calls this every hour; it runs
# only once per VERSION (marker file). Never touches the trading bot's state or logs.
set -uo pipefail
APP=/opt/kalshi-m15/app
V=$(grep -m1 '^VERSION' "$APP/wxbot/latency_probe.py" | cut -d'"' -f2)
MARK=/var/lib/kalshi-latency-probe.$V
[ -f "$MARK" ] && exit 0
touch "$MARK"
systemd-run --wait --collect --quiet -p User=kalshi -p EnvironmentFile=/etc/kalshi-m15/env \
  -p WorkingDirectory="$APP" -p RuntimeMaxSec=300 -p MemoryMax=200M \
  /opt/kalshi-m15/venv/bin/python -m wxbot.latency_probe || echo "latency probe failed"
true
