#!/usr/bin/env bash
# Hourly (systemd timer): pull the latest code/config from main; restart the bot if it changed.
#
# Safe deploy (Nov 10): real money runs on whatever main holds, and several threads merge
# to main every day. So a new version only goes live if it (1) compiles, imports and its
# config parses, and (2) the bot is still running ~45 s after the restart. Otherwise the
# server goes back to the version that was running, sends one phone alert, and skips that
# same code until main's code changes again. The kill switch and m15_control.json are read
# over the network by the bot itself, so holding back an update never blocks them.
set -euo pipefail
APP=/opt/kalshi-m15/app
PY=/opt/kalshi-m15/venv/bin/python
BAD=/var/lib/kalshi-m15-update.bad
CODE="wxbot bot.py config.toml requirements.txt requirements-m15.txt deploy/m15"
g() { sudo -u kalshi git -C "$APP" "$@"; }
sig() { g ls-tree "$1" -- $CODE | sha1sum | cut -d' ' -f1; }   # changes only when code/config does
alert() {   # one push per bad version; never fails the script
  logger -p user.err -t kalshi-m15-update "$1" || true
  ( set +u; set -a; . /etc/kalshi-m15/env 2>/dev/null; set +a
    [ -n "$NTFY_TOPIC" ] && curl -fsS -m 10 -H "Title: 15-min bot: update held back" -H "Priority: 4" \
      -d "$1" "${NTFY_SERVER:-https://ntfy.sh}/$NTFY_TOPIC" >/dev/null ) || true
}
preflight() {
  sudo -u kalshi "$PY" -m compileall -q "$APP/wxbot" "$APP/bot.py" >/dev/null &&
  ( cd "$APP" && sudo -u kalshi "$PY" -c "
import tomllib
tomllib.load(open('config.toml', 'rb'))
import wxbot.m15, wxbot.m15_feeds, wxbot.watch
" )
}
rollback() {   # $1 = commit to return to, $2 = new commit's code signature, $3 = reason
  g reset -q --hard "$1"
  echo "$2" > "$BAD"
  alert "$3 Kept the previous version ($(echo "$1" | cut -c1-7)); trading continues on it."
}

before=$(g rev-parse HEAD)
g fetch -q origin main
after=$(g rev-parse origin/main)
# Files the 15-min bot itself never loads: changing only these needs no bot restart (the
# watcher is a separate 15-min timer job), so the trading bot keeps running untouched.
NO_RESTART='^(wxbot/(watch|lip|lip_rank|paperlab|copycat|copycat_boss|copycat_live|ai_budget)\.py|deploy/m15/(update\.sh|install-(watch|lip|paperlab)\.sh|set-anthropic-key\.sh|kalshi-watch\.(service|timer)|kalshi-(lip|paper)\.service))$'
if [ "$before" != "$after" ] && g diff --name-only "$before" "$after" -- $CODE | grep -q . \
   && ! g diff --name-only "$before" "$after" -- $CODE | grep -Evq "$NO_RESTART"; then
  g reset -q --hard "$after"
  if ! preflight; then
    rollback "$before" "$(sig "$after")" "New watcher code from main failed its start-up check and was NOT used."
  else
    echo "updated $before -> $after (watcher/deploy only, bot not restarted)"
  fi
elif [ "$before" != "$after" ] && g diff --name-only "$before" "$after" -- $CODE | grep -q .; then
  newsig=$(sig "$after")
  if [ -f "$BAD" ] && [ "$(cat "$BAD")" = "$newsig" ]; then
    echo "main still has the code that failed here ($newsig); staying on $before"
  else
    g reset -q --hard "$after"
    sudo -u kalshi /opt/kalshi-m15/venv/bin/pip install -q -r "$APP/requirements.txt" -r "$APP/requirements-m15.txt"
    if ! preflight; then
      rollback "$before" "$newsig" "New code from main failed its start-up check and was NOT started."
    else
      install -m 644 "$APP/deploy/m15/kalshi-m15.service" /etc/systemd/system/
      systemctl daemon-reload
      systemctl restart kalshi-m15
      sleep 5;  pid1=$(systemctl show -p MainPID --value kalshi-m15)
      sleep 40; pid2=$(systemctl show -p MainPID --value kalshi-m15)
      if [ "$pid1" = "0" ] || [ "$pid1" != "$pid2" ] || ! systemctl is-active --quiet kalshi-m15; then
        rollback "$before" "$newsig" "New code from main kept crashing after the restart."
        install -m 644 "$APP/deploy/m15/kalshi-m15.service" /etc/systemd/system/
        systemctl daemon-reload
        systemctl restart kalshi-m15
      else
        rm -f "$BAD"
        echo "updated $before -> $after, restarted"
      fi
    fi
  fi
else
  # data-only commits: keep the checkout current (no restart; avoids hourly "started" pushes)
  g reset -q --hard "$after"
fi
# AI ops watcher timer (no-op when already installed; it does nothing without ANTHROPIC_API_KEY)
bash "$APP/deploy/m15/install-watch.sh" || echo "watch install failed"
# Source shadow logger (logging only; never trades, never reads the Kalshi key)
bash "$APP/deploy/m15/install-srclog.sh" || echo "srclog install failed"
# Copycat in shadow mode (logging only; never trades, never reads the Kalshi key)
bash "$APP/deploy/m15/install-copycat.sh" || echo "copycat install failed"
# Liquidity-reward test (real money, small; lip_control.json on main switches it)
bash "$APP/deploy/m15/install-lip.sh" || echo "lip install failed"
# Paper tests (no orders: GET requests only): wide-spread maker + parlay quote requests
bash "$APP/deploy/m15/install-paperlab.sh" || echo "paperlab install failed"
# One-time order speed test (1c post-only bids, cancelled at once; runs once per version)
bash "$APP/deploy/m15/run-latency-probe.sh" || echo "latency probe failed"
