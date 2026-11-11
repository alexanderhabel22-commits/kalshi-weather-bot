#!/usr/bin/env bash
# One-time order speed test (wxbot/latency_probe.py). update.sh calls this every hour; it runs
# only once per VERSION (marker file). Never touches the trading bot's state or logs.
# Log: /var/lib/kalshi-latency-probe.log
set -uo pipefail
APP=/opt/kalshi-m15/app
V=$(grep -m1 '^VERSION' "$APP/wxbot/latency_probe.py" | cut -d'"' -f2)
MARK=/var/lib/kalshi-latency-probe.$V
[ -f "$MARK" ] && exit 0
touch "$MARK"
cd "$APP" && runuser -u kalshi -- bash -c 'set -a; . /etc/kalshi-m15/env; set +a;
  exec timeout 300 /opt/kalshi-m15/venv/bin/python -m wxbot.latency_probe' \
  > /var/lib/kalshi-latency-probe.log 2>&1 || echo "latency probe failed (see /var/lib/kalshi-latency-probe.log)"
true
