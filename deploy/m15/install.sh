#!/usr/bin/env bash
# One-time setup of the always-on 15-minute bot on a fresh Ubuntu server.
#   curl -fsSL https://raw.githubusercontent.com/alexanderhabel22-commits/kalshi-weather-bot/main/deploy/m15/install.sh | sudo bash
# Safe to run again (it keeps your settings). Starts in whatever mode config.toml [m15] says.
set -euo pipefail
REPO=https://github.com/alexanderhabel22-commits/kalshi-weather-bot.git
HOME_DIR=/opt/kalshi-m15
ENV=/etc/kalshi-m15/env

echo "== Installing Python and git =="
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3-venv git curl >/dev/null
timedatectl set-ntp true 2>/dev/null || true

id kalshi >/dev/null 2>&1 || useradd --system --create-home --home-dir "$HOME_DIR" --shell /usr/sbin/nologin kalshi
if [ ! -d "$HOME_DIR/app/.git" ]; then
  sudo -u kalshi git clone -q "$REPO" "$HOME_DIR/app"
else
  sudo -u kalshi git -C "$HOME_DIR/app" pull -q --ff-only || true
fi
[ -d "$HOME_DIR/venv" ] || sudo -u kalshi python3 -m venv "$HOME_DIR/venv"
sudo -u kalshi "$HOME_DIR/venv/bin/pip" install -q -r "$HOME_DIR/app/requirements.txt" -r "$HOME_DIR/app/requirements-m15.txt"
sudo -u kalshi mkdir -p "$HOME_DIR/data"

mkdir -p /etc/kalshi-m15
if [ ! -f "$ENV" ]; then
  echo
  echo "== Two settings (paste each, then press Enter) =="
  read -r -p "ntfy topic for phone alerts (same one the bot already uses): " NTFY < /dev/tty
  read -r -p "GitHub token for uploading results (starts with github_pat_): " GHT < /dev/tty
  umask 077
  cat > "$ENV" <<EOV
NTFY_TOPIC=$NTFY
GITHUB_TOKEN=$GHT
M15_DATA_DIR=$HOME_DIR/data
WXBOT_DATA_DIR=$HOME_DIR/data/wxbot
PYTHONUNBUFFERED=1
EOV
  chown root:kalshi "$ENV"; chmod 640 "$ENV"
fi

install -m 644 "$HOME_DIR/app/deploy/m15/kalshi-m15.service" /etc/systemd/system/
install -m 644 "$HOME_DIR/app/deploy/m15/kalshi-m15-update.service" /etc/systemd/system/
install -m 644 "$HOME_DIR/app/deploy/m15/kalshi-m15-update.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now kalshi-m15-update.timer >/dev/null
systemctl enable kalshi-m15 >/dev/null
systemctl restart kalshi-m15
sleep 5
echo
if systemctl is-active --quiet kalshi-m15; then
  echo "== DONE: the 15-minute bot is running. You should get a '15-min bot started' push. =="
else
  echo "== The bot did not start. Copy the lines below and send them to Claude: =="
  journalctl -u kalshi-m15 -n 30 --no-pager
fi
