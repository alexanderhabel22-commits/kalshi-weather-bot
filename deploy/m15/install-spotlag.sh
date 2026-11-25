#!/usr/bin/env bash
# Installs/refreshes the DOGE/NEAR spot-lag PAPER logger (idempotent; update.sh calls it every hour).
# Separate service from the trading bot: if it fails, the 15-min bot is not affected.
# Turn it off: sudo systemctl disable --now kalshi-spotlag && sudo touch /var/lib/kalshi-spotlag.disabled
set -euo pipefail
APP=/opt/kalshi-m15/app
U=kalshi-spotlag.service
STAMP=/var/lib/kalshi-spotlag.codesig
[ -f /var/lib/kalshi-spotlag.disabled ] && exit 0
# Retired from the repo (Nov 25, 2026: spot-lag stopped at -9.7c/ct, all paper fills settled; data stays on
# m15-data). Delete deploy/m15/spotlag.off to bring it back.
if [ -f "$APP/deploy/m15/spotlag.off" ]; then
  systemctl disable --now "$U" >/dev/null 2>&1 || true
  exit 0
fi
changed=0
if ! cmp -s "$APP/deploy/m15/$U" "/etc/systemd/system/$U"; then
  install -m 644 "$APP/deploy/m15/$U" /etc/systemd/system/
  systemctl daemon-reload
  changed=1
fi
sig=$(cat "$APP/wxbot/spotlag.py" "$APP/wxbot/m15_fv.py" | sha1sum | cut -d' ' -f1)
if ! systemctl is-enabled --quiet "$U" 2>/dev/null; then
  systemctl enable --now "$U" >/dev/null
elif [ "$changed" = 1 ] || [ "$(cat "$STAMP" 2>/dev/null)" != "$sig" ]; then
  systemctl restart "$U"
fi
echo "$sig" > "$STAMP"
true
