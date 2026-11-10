#!/usr/bin/env bash
# Installs/refreshes the AI ops watcher timer (idempotent; update.sh calls it every hour).
set -euo pipefail
APP=/opt/kalshi-m15/app
changed=0
for u in kalshi-watch.service kalshi-watch.timer; do
  if ! cmp -s "$APP/deploy/m15/$u" "/etc/systemd/system/$u"; then
    install -m 644 "$APP/deploy/m15/$u" /etc/systemd/system/
    changed=1
  fi
done
if ! /opt/kalshi-m15/venv/bin/python -c "import anthropic" 2>/dev/null; then
  sudo -u kalshi /opt/kalshi-m15/venv/bin/pip install -q -r "$APP/requirements-m15.txt"
fi
[ "$changed" = 1 ] && systemctl daemon-reload
systemctl is-enabled --quiet kalshi-watch.timer 2>/dev/null || systemctl enable --now kalshi-watch.timer >/dev/null
true
