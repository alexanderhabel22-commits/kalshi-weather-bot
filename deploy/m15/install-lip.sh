#!/usr/bin/env bash
# Installs/refreshes the liquidity-reward test bot (idempotent; update.sh calls it every hour).
# Separate service from the 15-min bot: if it fails, the 15-min bot is not affected. Its switch
# is lip_control.json on main (mode off cancels every bid within ~5 min).
# Turn it off: sudo systemctl disable --now kalshi-lip
set -euo pipefail
APP=/opt/kalshi-m15/app
U=kalshi-lip.service
STAMP=/var/lib/kalshi-lip.codesig
[ -f /var/lib/kalshi-lip.disabled ] && exit 0
changed=0
if ! cmp -s "$APP/deploy/m15/$U" "/etc/systemd/system/$U"; then
  install -m 644 "$APP/deploy/m15/$U" /etc/systemd/system/
  systemctl daemon-reload
  changed=1
fi
sig=$(sha1sum "$APP/wxbot/lip.py" "$APP/wxbot/lip_rank.py" "$APP/wxbot/kalshi_api.py" "$APP/wxbot/kalshi.py" "$APP/bot.py" | sha1sum | cut -d' ' -f1)
if ! systemctl is-enabled --quiet "$U" 2>/dev/null; then
  systemctl enable --now "$U" >/dev/null
elif [ "$changed" = 1 ] || [ "$(cat "$STAMP" 2>/dev/null)" != "$sig" ]; then
  systemctl restart "$U"
fi
echo "$sig" > "$STAMP"
true
