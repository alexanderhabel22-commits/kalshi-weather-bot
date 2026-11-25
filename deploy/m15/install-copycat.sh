#!/usr/bin/env bash
# Installs/refreshes the Copycat shadow logger (idempotent; update.sh calls it every hour).
# Separate service from the trading bot: if it fails, the 15-min bot is not affected.
# Turn it off: deploy/m15/copycat.off in the repo (or sudo systemctl disable --now kalshi-copycat)
set -euo pipefail
APP=/opt/kalshi-m15/app
U=kalshi-copycat.service
STAMP=/var/lib/kalshi-copycat.codesig
[ -f /var/lib/kalshi-copycat.disabled ] && exit 0
# Retired from the repo (Nov 25, 2026, Alex's choice: real copies off since Nov 16, paper copying showed no
# way to pick winners; frees ~650 MB for the 1 GB server). Data stays on copycat-data.
# Delete deploy/m15/copycat.off to bring it back.
if [ -f "$APP/deploy/m15/copycat.off" ]; then
  systemctl disable --now "$U" >/dev/null 2>&1 || true
  exit 0
fi
changed=0
if ! cmp -s "$APP/deploy/m15/$U" "/etc/systemd/system/$U"; then
  install -m 644 "$APP/deploy/m15/$U" /etc/systemd/system/
  systemctl daemon-reload
  changed=1
fi
sig=$(sha1sum "$APP/wxbot/copycat.py" "$APP/wxbot/copycat_live.py" "$APP/wxbot/copycat_boss.py" "$APP/wxbot/srclog.py" "$APP/bot.py" | sha1sum | cut -d' ' -f1)
if ! systemctl is-enabled --quiet "$U" 2>/dev/null; then
  systemctl enable --now "$U" >/dev/null
elif [ "$changed" = 1 ] || [ "$(cat "$STAMP" 2>/dev/null)" != "$sig" ]; then
  systemctl restart "$U"
fi
echo "$sig" > "$STAMP"
true
