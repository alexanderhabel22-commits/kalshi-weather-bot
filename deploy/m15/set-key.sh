#!/usr/bin/env bash
# Adds the Kalshi API key for real orders (and the fast Kalshi feed).
#   sudo bash /opt/kalshi-m15/app/deploy/m15/set-key.sh
set -euo pipefail
ENV=/etc/kalshi-m15/env
KEY=/etc/kalshi-m15/kalshi.pem
read -r -p "Kalshi API key ID: " KID < /dev/tty
echo "Now paste the whole private key (from -----BEGIN to -----END lines), then press Enter:"
umask 077
: > "$KEY.tmp"
while IFS= read -r line < /dev/tty; do
  echo "$line" >> "$KEY.tmp"
  case "$line" in *"-----END"*) break ;; esac
done
grep -q "BEGIN" "$KEY.tmp" || { echo "That didn't look like a key (no BEGIN line). Nothing changed."; rm -f "$KEY.tmp"; exit 1; }
mv "$KEY.tmp" "$KEY"; chown root:kalshi "$KEY"; chmod 640 "$KEY"
sed -i '/^KALSHI_API_KEY_ID=/d;/^KALSHI_PRIVATE_KEY_FILE=/d' "$ENV"
printf 'KALSHI_API_KEY_ID=%s\nKALSHI_PRIVATE_KEY_FILE=%s\n' "$KID" "$KEY" >> "$ENV"
systemctl restart kalshi-m15
echo "== Key saved and bot restarted. =="
