#!/usr/bin/env bash
# Adds the Claude API key for the AI ops watcher, then runs one check.
#   sudo bash /opt/kalshi-m15/app/deploy/m15/set-anthropic-key.sh
set -euo pipefail
ENV=/etc/kalshi-m15/env
APP=/opt/kalshi-m15/app
read -r -s -p "Paste the Claude API key (starts with sk-ant-), then press Enter: " KEY < /dev/tty
echo
case "$KEY" in sk-ant-*) ;; *) echo "That doesn't start with sk-ant-. Nothing changed."; exit 1 ;; esac
sed -i '/^ANTHROPIC_API_KEY=/d' "$ENV"
printf 'ANTHROPIC_API_KEY=%s\n' "$KEY" >> "$ENV"
bash "$APP/deploy/m15/install-watch.sh"
echo "== Running one check now (takes ~20 seconds) =="
systemctl start kalshi-watch.service || true
journalctl -u kalshi-watch -n 5 --no-pager -o cat
echo "== Done. The watcher now runs every 15 minutes. =="
