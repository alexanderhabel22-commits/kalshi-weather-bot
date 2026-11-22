#!/usr/bin/env bash
# Installs/refreshes the shipping release-window service (paper log + small real test switched by
# shiprelease_control.json; idempotent; update.sh calls it every hour). Separate service: if it fails,
# nothing else is affected.
# Turn it off: sudo systemctl disable --now kalshi-shiprel && sudo touch /var/lib/kalshi-shiprel.disabled
set -euo pipefail
APP=/opt/kalshi-m15/app
U=kalshi-shiprel.service
STAMP=/var/lib/kalshi-shiprel.codesig
[ -f /var/lib/kalshi-shiprel.disabled ] && exit 0
changed=0
if ! cmp -s "$APP/deploy/m15/$U" "/etc/systemd/system/$U"; then
  install -m 644 "$APP/deploy/m15/$U" /etc/systemd/system/
  systemctl daemon-reload
  changed=1
fi
sig=$(sha1sum "$APP/wxbot/shiprelease.py" | sha1sum | cut -d' ' -f1)
if ! systemctl is-enabled --quiet "$U" 2>/dev/null; then
  systemctl enable --now "$U" >/dev/null
elif [ "$changed" = 1 ] || [ "$(cat "$STAMP" 2>/dev/null)" != "$sig" ]; then
  systemctl restart "$U"
fi
echo "$sig" > "$STAMP"
true
