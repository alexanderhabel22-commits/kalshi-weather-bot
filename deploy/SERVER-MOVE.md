# Moving the 15-minute bot server to a smaller droplet (ready-to-run plan)

**Status:** prepared Nov 25, 2026, NOT scheduled. Run it only when Alex says go.

**Why a move and not a resize:** kalshi-m15 was created on the $12 plan with a 50 GB disk.
DigitalOcean can never shrink a disk, so the Resize page greys out the $6 and $4 plans
(checked Nov 25). The only way to $6/month is a new $6 droplet with the bots moved onto it.

**Saves:** $6/month ($72/year). **Costs:** ~20 minutes of Alex pasting lines, and the
server bots (natgas 15-min, fast quake, shipping release, websocket recorder, watcher)
are down for about 10 minutes. The main bot on GitHub Actions is not affected.

**Fits in 1 GB?** Yes, after PR #370 turned off Copycat, paper lab, rewards and spot-lag.
What's left (15-min bot, quakefast, shiprel, wsfeed, watcher) is about 350-450 MB.
Check `services` in m15-data `m15/watch.json` before starting; if anything big was
switched back on, re-check.

---

## The one rule

**Never let both servers run the bots at the same time.** They share the same Kalshi
key, caps and data branch, so two copies would double-trade and fight over uploads.
The old server is stopped (and disabled) BEFORE the new one is started.

---

## Step 0 (Claude, before telling Alex to start)

- Pick a quiet time: no open 15-min natgas or quake bets, not a Tuesday (shipping release
  day), not during a Fed/CPI release. Check open positions first.
- Look up the old droplet's IP address on the DigitalOcean droplets page and put it in the
  lines below in place of `OLD_IP`.

## Step 1: Create the new droplet (Alex, or Claude in the browser with Alex's OK)

DigitalOcean: **Create → Droplets**
- Region **New York, NYC1** (same as now). Image **Ubuntu 24.04 (LTS) x64**.
- Size **Basic → Regular → $6/mo (1 GB / 25 GB)**.
- Authentication **Password**. Alex types a new root password and saves it.
  (Claude never types passwords.)
- Hostname `kalshi-m15-small`. **Create Droplet**, wait ~1 minute.

Billing for the new droplet starts here (~$0.009/hour).

## Step 2: Stop and pack the OLD server (old droplet's console)

Paste as one block:

```
for u in kalshi-m15-update.timer kalshi-watch.timer kalshi-m15 kalshi-quakefast kalshi-shiprel kalshi-wsfeed kalshi-copycat kalshi-lip kalshi-paper kalshi-spotlag kalshi-srclog; do sudo systemctl disable --now $u 2>/dev/null; done; sleep 3
systemctl list-units --type=service --state=running | grep kalshi || echo "ALL KALSHI SERVICES STOPPED"
sudo tar czf /root/move.tgz /etc/kalshi-m15 /opt/kalshi-m15/data && ls -lh /root/move.tgz
```

It must print `ALL KALSHI SERVICES STOPPED` and the size of move.tgz.
The bundle holds the settings file (keys) and the bot's local data. It never leaves the
two servers.

## Step 3: Bring the bundle over and start the NEW server (new droplet's console)

```
sudo useradd --system --create-home --home-dir /opt/kalshi-m15 --shell /usr/sbin/nologin kalshi
scp -o StrictHostKeyChecking=accept-new root@OLD_IP:/root/move.tgz /root/
```

It asks for the **old** droplet's root password (typed by Alex). Then:

```
sudo tar xzf /root/move.tgz -C / && sudo rm /root/move.tgz
curl -fsSL https://raw.githubusercontent.com/alexanderhabel22-commits/kalshi-weather-bot/main/deploy/m15/install.sh | sudo bash
sudo bash /opt/kalshi-m15/app/deploy/m15/update.sh
systemctl list-units --type=service --state=running | grep kalshi
```

install.sh does not ask questions this time (the settings file came over), installs
Python and the code (~3-5 minutes) and starts the 15-min bot. update.sh then installs the
side services right away instead of waiting an hour. The last line should list
kalshi-m15, kalshi-quakefast, kalshi-shiprel and kalshi-wsfeed as running.
Alex's phone gets a "15-min bot started" push.

**If scp is refused** (password login closed on the old droplet): skip it, run install.sh
anyway (it asks for the ntfy topic and GitHub token), then re-add the keys with
`set-key.sh` (Kalshi, needs the key file or a new Kalshi key), `set-anthropic-key.sh`
and `set-oanda.sh`. Local data then starts empty, which only loses local CSV history
already uploaded to m15-data.

## Step 4: Check (Claude)

Within 30 minutes: new uploads on the m15-data branch, `watch.json` shows the new
host's memory under 1 GB, natgas/quake/shiprel logs moving, no "update held back" push.

## Step 5: Retire the old droplet (after 2-3 good days)

- Rollback until then: on the new console paste the same `for u in ...` stop line from
  Step 2, then on the old console
  `sudo systemctl enable --now kalshi-m15 kalshi-m15-update.timer && sudo bash /opt/kalshi-m15/app/deploy/m15/update.sh`.
- A powered-off droplet is still billed, so the saving only starts once it is destroyed.
- **Destroy is permanent and needs Alex's OK at the time.** DigitalOcean: kalshi-m15
  → Settings → Destroy. Do NOT touch the other droplet, "trading-office" (Alex's own).
- Afterwards update HANDOFF.md and project memory (droplet name/size), and rename the
  new droplet to `kalshi-m15` if wanted (DigitalOcean allows renaming any time).
