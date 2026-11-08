# Setting up the 15-minute bot server (about 15 minutes, no coding)

You'll do everything in your web browser on your PC. There are 3 parts. Part 3 (the
Kalshi key) can wait until the bot has run on paper for a day.

**Cost:** a DigitalOcean "Basic" server, about **$4/month** (the $6 one is fine too).
**Break-even:** at +4c per contract that's about 100 fills a month. BTC alone should
make 1,000+ fills a month at 1 contract each, so the server is a small cost if the edge
is real. If the edge turns out to be zero, we lose the $4-6 a month and switch the server off.

---

## Part 1: GitHub token (lets the bot upload its results so Claude can grade them)

1. Go to **github.com → your picture (top right) → Settings → Developer settings →
   Personal access tokens → Fine-grained tokens → Generate new token**.
2. Name it `kalshi-m15`. Expiration: **1 year**.
3. Repository access: **Only select repositories → kalshi-weather-bot**.
4. Permissions → Repository permissions → **Contents: Read and write**. Nothing else.
5. Click **Generate token** and copy it (it starts with `github_pat_`). Keep the tab open.

## Part 2: The server

1. Sign up at **digitalocean.com** (card needed).
2. Click **Create → Droplets**.
   - Region: **New York** (closest to Kalshi).
   - Image: **Ubuntu 24.04 (LTS)**.
   - Size: **Basic → Regular → the $4/mo one** (or $6/mo if $4 isn't offered).
   - Authentication: **Password** and pick a strong root password (save it somewhere).
   - Hostname: `kalshi-m15`.
   - Click **Create Droplet** and wait about 1 minute.
3. Click the droplet, then **Access → Launch Droplet Console**. A black terminal window opens in your browser.
4. Paste this line (right-click → Paste, or Ctrl+Shift+V) and press **Enter**:

   ```
   curl -fsSL https://raw.githubusercontent.com/alexanderhabel22-commits/kalshi-weather-bot/main/deploy/m15/install.sh | sudo bash
   ```

5. When it asks:
   - **ntfy topic**: the same topic your phone already gets bot alerts on.
   - **GitHub token**: paste the `github_pat_...` token from Part 1.
6. It ends with **"DONE: the 15-minute bot is running"**, and your phone gets a
   **"15-min bot started"** push. If it shows an error instead, screenshot it and send it to Claude.

That's it for the paper run. The server updates itself from GitHub every hour, so Claude
can ship fixes and settings changes without you doing anything.

## Part 3: The Kalshi key (only for real money, after the paper shakedown)

Use a **new, separate** key for the server, so it can be revoked on its own.

1. On **kalshi.com → Account → API Keys → Create key**. Save the key file it downloads
   and copy the **Key ID**.
2. Open the downloaded key file in **Notepad** and copy everything (Ctrl+A, Ctrl+C).
3. In the droplet console, paste this and press Enter:

   ```
   sudo bash /opt/kalshi-m15/app/deploy/m15/set-key.sh
   ```

4. Paste the Key ID and press Enter. Then paste the whole key and press Enter.
5. It prints "Key saved and bot restarted". Tell Claude, and Claude switches the
   bot to live once the paper day looks right.

---

## Stopping it

- **Everything (all bots):** set the GitHub variable `TRADING_MODE` to `off`, the same kill
  switch as before. The server bot follows it within about 10 minutes and cancels its orders.
- **Just this bot, right now:** in the droplet console run `sudo systemctl stop kalshi-m15`.
- **For good:** delete the droplet in DigitalOcean (billing stops) and delete the Kalshi key.
