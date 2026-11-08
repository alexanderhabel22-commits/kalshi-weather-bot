#!/usr/bin/env bash
# Hourly (systemd timer): pull the latest code/config from main; restart the bot if it changed.
set -euo pipefail
APP=/opt/kalshi-m15/app
before=$(sudo -u kalshi git -C "$APP" rev-parse HEAD)
sudo -u kalshi git -C "$APP" fetch -q origin main
sudo -u kalshi git -C "$APP" reset -q --hard origin/main
after=$(sudo -u kalshi git -C "$APP" rev-parse HEAD)
if [ "$before" != "$after" ]; then
  sudo -u kalshi /opt/kalshi-m15/venv/bin/pip install -q -r "$APP/requirements.txt" -r "$APP/requirements-m15.txt"
  install -m 644 "$APP/deploy/m15/kalshi-m15.service" /etc/systemd/system/
  systemctl daemon-reload
  systemctl restart kalshi-m15
  echo "updated $before -> $after, restarted"
fi
