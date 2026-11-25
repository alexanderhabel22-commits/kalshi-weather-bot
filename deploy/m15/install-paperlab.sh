#!/usr/bin/env bash
# Installs/refreshes the paper-test service (no orders) (idempotent; update.sh calls it every hour).
# Separate service: if it fails, nothing else is affected.
# Turn it off: deploy/m15/paper.off in the repo (or sudo systemctl disable --now kalshi-paper)
set -euo pipefail
APP=/opt/kalshi-m15/app
U=kalshi-paper.service
STAMP=/var/lib/kalshi-paper.codesig
[ -f /var/lib/kalshi-paper.disabled ] && exit 0
# Retired from the repo (Nov 25, 2026: maker, parlay-quote and sell-the-field paper tests all judged dead;
# their data stays on m15-data). Delete deploy/m15/paper.off to bring it back.
if [ -f "$APP/deploy/m15/paper.off" ]; then
  systemctl disable --now "$U" >/dev/null 2>&1 || true
  exit 0
fi
changed=0
if ! cmp -s "$APP/deploy/m15/$U" "/etc/systemd/system/$U"; then
  install -m 644 "$APP/deploy/m15/$U" /etc/systemd/system/
  systemctl daemon-reload
  changed=1
fi
sig=$(sha1sum "$APP/wxbot/paperlab.py" "$APP/wxbot/m15.py" "$APP/wxbot/kalshi.py" "$APP/bot.py" | sha1sum | cut -d' ' -f1)
if ! systemctl is-enabled --quiet "$U" 2>/dev/null; then
  systemctl enable --now "$U" >/dev/null
elif [ "$changed" = 1 ] || [ "$(cat "$STAMP" 2>/dev/null)" != "$sig" ]; then
  systemctl restart "$U"
fi
echo "$sig" > "$STAMP"
true
