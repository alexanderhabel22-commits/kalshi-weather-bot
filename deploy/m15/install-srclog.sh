#!/usr/bin/env bash
# Installs/refreshes the source shadow logger (idempotent; update.sh calls it every hour).
# Separate service from the trading bot: if it fails, the 15-min bot is not affected.
# Turn it off: sudo systemctl disable --now kalshi-srclog
set -euo pipefail
APP=/opt/kalshi-m15/app
U=kalshi-srclog.service
STAMP=/var/lib/kalshi-srclog.codesig
[ -f /var/lib/kalshi-srclog.disabled ] && exit 0
changed=0
if ! cmp -s "$APP/deploy/m15/$U" "/etc/systemd/system/$U"; then
  install -m 644 "$APP/deploy/m15/$U" /etc/systemd/system/
  systemctl daemon-reload
  changed=1
fi
sig=$(sha1sum "$APP/wxbot/srclog.py" "$APP/bot.py" | sha1sum | cut -d' ' -f1)
if ! systemctl is-enabled --quiet "$U" 2>/dev/null; then
  systemctl enable --now "$U" >/dev/null
elif [ "$changed" = 1 ] || [ "$(cat "$STAMP" 2>/dev/null)" != "$sig" ]; then
  systemctl restart "$U"
fi
echo "$sig" > "$STAMP"
true
