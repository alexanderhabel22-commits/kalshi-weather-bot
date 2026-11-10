#!/usr/bin/env bash
# Adds the OANDA practice-account API token (metals/oil prices for the jump guard).
#   sudo bash /opt/kalshi-m15/app/deploy/m15/set-oanda.sh
set -euo pipefail
ENV=/etc/kalshi-m15/env
read -r -s -p "Paste the OANDA API token (it stays hidden), then press Enter: " TOK < /dev/tty
echo
TOK=$(echo "$TOK" | tr -d '[:space:]')
[ ${#TOK} -ge 20 ] || { echo "That looks too short for a token. Nothing changed."; exit 1; }
OENV=""
for e in practice:api-fxpractice live:api-fxtrade; do
  code=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $TOK" \
         "https://${e#*:}.oanda.com/v3/accounts" || true)
  if [ "$code" = "200" ]; then OENV=${e%%:*}; break; fi
done
if [ -z "$OENV" ]; then
  echo "OANDA rejected that token (last answer $code). Nothing changed."
  exit 1
fi
sed -i '/^OANDA_TOKEN=/d;/^OANDA_ENV=/d' "$ENV"
umask 077
printf 'OANDA_TOKEN=%s\nOANDA_ENV=%s\n' "$TOK" "$OENV" >> "$ENV"
systemctl restart kalshi-m15
echo "== OANDA token works ($OENV account), saved, bot restarted. =="
