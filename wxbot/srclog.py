"""Shadow logger for data-settled Kalshi markets (Nov 10, 2026; pro-trader report idea 3).

SHADOW ONLY: nothing here places, changes or cancels an order, and it never reads the Kalshi
trading key. It answers one question with real timestamps: when a market's settlement source
changes (a new RCP approval average, a new #1 on the Arena leaderboard, Netflix's weekly Top 10
file, TSA's daily count, a Rotten Tomatoes score), how many seconds pass before we see it, and
what were the Kalshi prices at that moment and just after.

Why these sources: a replay of Mar-Nov 2026 trade tapes (research_src/race.py) found that
repricings in these series take seconds to minutes, and that a lot of contracts still trade at
stale prices 3 s to 5 min after the first move (Trump RCP approval: about $10k of taker profit
a month across all traders in that window). The first ~3 s goes to faster bots. The open
question this logger settles: can a 5-15 s poller see the change early enough to matter?

How it works (one thread, polite polling, a few hundred KB of traffic a minute):
  - each Source is fetched on its own interval (faster in the hours it usually changes);
  - a "key" is pulled out of the response (the part that decides settlement); when the key
    changes we log a change row, save the raw page (gzipped, rate-limited), and snapshot the
    linked Kalshi series' open markets (best bid/ask) now and at +10 s, +30 s, +60 s, +300 s;
  - every fetch result (status, bytes, key hash) goes in a compact poll log, so blocked sources
    (403s) show up in the data instead of failing silently;
  - hourly files srclog-YYYY-MM-DD-HH.jsonl(.gz) are uploaded to branch srclog-data once the
    hour ends (needs GITHUB_TOKEN; without it files stay on disk).

Run: python bot.py srclog   (systemd unit deploy/m15/kalshi-srclog.service on the server)
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import html as htmllib
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

import requests

log = logging.getLogger("wxbot")
ET = ZoneInfo("America/New_York")
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
REPO = "alexanderhabel22-commits/kalshi-weather-bot"
BRANCH = "srclog-data"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0 Safari/537.36")
SNAP_DELAYS = (0, 10, 30, 60, 300)
RAW_MIN_GAP_S = 600          # at most one raw page saved per source per 10 min
RAW_MAX_BYTES = 400_000_000  # stop saving raw pages past ~400 MB on disk


# ------------------------------------------------------------------ key extraction

_TAGS = re.compile(r"<(script|style|noscript)\b.*?</\1>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def visible_text(page: str) -> str:
    """Page text without scripts, styles and tags (whitespace squeezed)."""
    t = _TAGS.sub(" ", page)
    t = _TAG.sub(" ", t)
    return _WS.sub(" ", htmllib.unescape(t)).strip()


def around(text: str, marker: str, after: int = 240, before: int = 0) -> str | None:
    i = text.find(marker)
    if i < 0:
        return None
    return text[max(0, i - before): i + len(marker) + after]


def key_rcp(r: requests.Response) -> str | None:
    """RealClearPolling approval page: the 'RCP Average' row (dates, approve, disapprove)."""
    t = visible_text(r.text)
    k = around(t, "RCP Average", 120)
    if k:
        return k
    m = re.search(r'"rcp_?average"\s*:\s*\{[^{}]{0,400}\}', r.text, re.I)
    return m.group(0) if m else None


def key_arena(r: requests.Response) -> str | None:
    """Arena text leaderboard: the first few model rows (rank, name, score, votes)."""
    t = visible_text(r.text)
    for marker in ("Rank (UB)", "Rank(UB)", "Rank"):
        k = around(t, marker, 600)
        if k and len(k) > 200:
            return re.sub(r"\b(updated|last updated)\b.{0,40}", "", k, flags=re.I)
    # app shell only: fall back to model/score pairs in the embedded data
    pairs = re.findall(r'"(?:model_?name|modelDisplayName|name)"\s*:\s*"([^"]{3,80})"[^{}]{0,300}?'
                       r'"(?:score|rating|arena_score)"\s*:\s*([0-9.]+)', r.text)
    return json.dumps(pairs[:15]) if pairs else None


def key_headers(r: requests.Response) -> str | None:
    """Static data files (Netflix TSV): Last-Modified + size + first/last bytes of the body."""
    body = r.content
    return "|".join([r.headers.get("last-modified", ""), str(len(body)),
                     hashlib.sha1(body[:4096] + body[-4096:]).hexdigest()])


def key_tsa(r: requests.Response) -> str | None:
    """TSA passenger volumes: the newest row of the table (date + count)."""
    t = visible_text(r.text)
    m = re.search(r"(\d{1,2}/\d{1,2}/\d{4})\s+([\d,]{7,})", t)
    return f"{m.group(1)} {m.group(2)}" if m else None


def key_rt(r: requests.Response) -> str | None:
    """Rotten Tomatoes title page: Tomatometer score + review count from the scorecard data."""
    out = []
    for pat in (r'"criticsScore"\s*:\s*\{[^{}]{0,600}\}', r'"tomatometer"\s*:\s*\{[^{}]{0,300}\}',
                r'"criticsAll"\s*:\s*\{[^{}]{0,600}\}'):
        m = re.search(pat, r.text)
        if m:
            out.append(m.group(0))
    if out:
        return "|".join(out)
    t = visible_text(r.text)
    return around(t, "Tomatometer", 80, 60)


def key_hf(r: requests.Response) -> str | None:
    """Hugging Face dataset info: the current commit sha (the nightly Arena dump)."""
    try:
        js = r.json()
    except ValueError:
        return None
    return f"{js.get('sha')} {js.get('lastModified')}"


def interval_weekday_hours(fast: float, slow: float, start_h: float, end_h: float,
                           weekdays_only: bool = False) -> Callable[[datetime], float]:
    def f(now_et: datetime) -> float:
        h = now_et.hour + now_et.minute / 60
        if weekdays_only and now_et.weekday() >= 5:
            return slow
        return fast if start_h <= h < end_h else slow
    return f


def interval_netflix(now_et: datetime) -> float:
    """Netflix posts its weekly Top 10 on Tuesdays (Last-Modified seen ~2:55 PM ET)."""
    if now_et.weekday() == 1 and 12 <= now_et.hour < 18:
        return 10
    return 300


@dataclass
class Source:
    name: str
    url: str
    key: Callable[[requests.Response], str | None]
    interval: Callable[[datetime], float]
    series: tuple[str, ...] = ()
    headers: dict = field(default_factory=dict)
    next_at: float = 0.0
    last_key: str | None = None
    last_status: int | None = None
    raw_at: float = 0.0
    fails: int = 0


def default_sources() -> list[Source]:
    return [
        Source("rcp_trump_approval",
               "https://www.realclearpolling.com/polls/approval/donald-trump/approval-rating",
               key_rcp, interval_weekday_hours(10, 60, 7.0, 14.0),
               ("KXTRUMPAPPROVE", "KXAPRPOTUS", "KXAPRPOTUSD")),
        Source("arena_text_nostyle", "https://arena.ai/leaderboard/text/overall-no-style-control",
               key_arena, interval_weekday_hours(15, 60, 9.0, 21.0),
               ("KXTOPMODEL", "KXLLM1", "KXCHINAAI")),
        Source("hf_arena_dataset", "https://huggingface.co/api/datasets/lmarena-ai/leaderboard-dataset",
               key_hf, lambda now: 300.0, ("KXTOPMODEL",)),
        Source("netflix_global_tsv", "https://www.netflix.com/tudum/top10/data/all-weeks-global.tsv",
               key_headers, interval_netflix,
               ("KXNETFLIXRANKSHOWGLOBAL", "KXNETFLIXRANKMOVIEGLOBAL")),
        Source("netflix_countries_tsv",
               "https://www.netflix.com/tudum/top10/data/all-weeks-countries.tsv",
               key_headers, interval_netflix,
               ("KXNETFLIXRANKSHOW", "KXNETFLIXRANKMOVIE", "KXNETFLIXRANKSHOWRUNNERUP",
                "KXNETFLIXRANKMOVIERUNNERUP")),
        Source("tsa_volumes", "https://www.tsa.gov/travel/passenger-volumes",
               key_tsa, interval_weekday_hours(15, 600, 6.0, 12.0), ("KXTSAD", "KXTSAW")),
    ]


# ------------------------------------------------------------------ the logger

def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


class SrcLog:
    def __init__(self, out_dir: Path, sources: list[Source] | None = None,
                 session: requests.Session | None = None, clock=time.time,
                 uploader: Callable[[str, bytes], None] | None = None,
                 rt_refresh_s: float = 3600):
        self.out = out_dir
        self.out.mkdir(parents=True, exist_ok=True)
        (self.out / "raw").mkdir(exist_ok=True)
        self.sources = sources if sources is not None else default_sources()
        self.s = session or requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
        self.clock = clock
        self.uploader = uploader
        self.snaps: list[tuple[float, str, str, int]] = []   # (due, source, series, delay)
        self.rt_refresh_s = rt_refresh_s
        self.rt_next = 0.0
        self.uploaded: set[str] = set()
        self._lock = threading.Lock()

    # -- files
    def _hour_file(self, ts: float) -> Path:
        d = datetime.fromtimestamp(ts, timezone.utc)
        return self.out / f"srclog-{d:%Y-%m-%d-%H}.jsonl"

    def write(self, row: dict) -> None:
        row.setdefault("ts", round(self.clock(), 3))
        row["et"] = datetime.fromtimestamp(row["ts"], ET).strftime("%Y-%m-%d %H:%M:%S")
        with self._lock, open(self._hour_file(row["ts"]), "a") as fh:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")

    def _raw_bytes(self) -> int:
        return sum(p.stat().st_size for p in (self.out / "raw").glob("*.gz"))

    def save_raw(self, src: Source, now: float, body: bytes) -> str | None:
        if now - src.raw_at < RAW_MIN_GAP_S or self._raw_bytes() > RAW_MAX_BYTES:
            return None
        src.raw_at = now
        d = datetime.fromtimestamp(now, timezone.utc)
        p = self.out / "raw" / f"{src.name}-{d:%Y%m%d-%H%M%S}.html.gz"
        p.write_bytes(gzip.compress(body))
        return p.name

    # -- Kalshi (public, unauthenticated market data only)
    def kalshi_snapshot(self, series: str) -> list[list]:
        r = self.s.get(f"{KALSHI}/markets", params={"series_ticker": series, "status": "open",
                                                   "limit": 200}, timeout=15)
        r.raise_for_status()
        out = []
        for m in r.json().get("markets") or []:
            def px(k):
                v = m.get(k + "_dollars", m.get(k))
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    return None
                return round(v / 100, 4) if v > 1.5 else v
            out.append([m.get("ticker"), px("yes_bid"), px("yes_ask"), px("last_price"),
                        m.get("volume_fp", m.get("volume"))])
        return out

    # -- one source
    def poll(self, src: Source, now: float) -> None:
        t0 = self.clock()
        try:
            r = self.s.get(src.url, timeout=20, headers=src.headers)
            status, body = r.status_code, r.content
        except requests.RequestException as exc:
            src.fails += 1
            self.write({"kind": "poll", "src": src.name, "status": 0, "err": str(exc)[:120]})
            return
        lat = round(self.clock() - t0, 3)
        key = src.key(r) if status == 200 else None
        h = hashlib.sha1(key.encode()).hexdigest()[:12] if key else None
        changed = key is not None and src.last_key is not None and key != src.last_key
        first = key is not None and src.last_key is None
        if status != src.last_status or changed or first or src.fails:
            # compact: only log polls when something is new (status change, key change)
            self.write({"kind": "poll", "src": src.name, "status": status, "bytes": len(body),
                        "lat": lat, "key": h, "age": r.headers.get("age"),
                        "lm": r.headers.get("last-modified")})
        src.last_status = status
        src.fails = 0
        if status == 200 and key is None:
            self.write({"kind": "nokey", "src": src.name, "bytes": len(body)})
            if now - src.raw_at >= 6 * 3600:            # keep one sample to fix the parser
                src.raw_at = now - RAW_MIN_GAP_S
                self.save_raw(src, now, body)
        if first or changed:
            raw = self.save_raw(src, now, body)
            self.write({"kind": "change" if changed else "first", "src": src.name,
                        "key": key[:600], "prev": (src.last_key or "")[:600] if changed else None,
                        "raw": raw, "series": list(src.series)})
            if changed:
                for se in src.series:
                    for dly in SNAP_DELAYS:
                        self.snaps.append((now + dly, src.name, se, dly))
            src.last_key = key

    def run_snaps(self, now: float) -> None:
        due = [x for x in self.snaps if x[0] <= now]
        self.snaps = [x for x in self.snaps if x[0] > now]
        for _, name, se, dly in due:
            try:
                rows = self.kalshi_snapshot(se)
                self.write({"kind": "kalshi", "src": name, "series": se, "delay": dly,
                            "markets": rows})
            except Exception as exc:  # noqa: BLE001
                self.write({"kind": "kalshi", "src": name, "series": se, "delay": dly,
                            "err": str(exc)[:120]})

    # -- Rotten Tomatoes: follow whatever titles Kalshi currently lists
    def refresh_rt(self, now: float) -> None:
        self.rt_next = now + self.rt_refresh_s
        try:
            r = self.s.get(f"{KALSHI}/events", params={"series_ticker": "KXRT", "status": "open",
                                                      "limit": 100}, timeout=15)
            r.raise_for_status()
            events = r.json().get("events") or []
        except Exception as exc:  # noqa: BLE001
            self.write({"kind": "rt_refresh", "err": str(exc)[:120]})
            return
        have = {s.name for s in self.sources}
        for ev in events:
            title = ev.get("title") or ""
            m = re.search(r"(?:score for|for)\s+(.+?)\??$", title) or re.search(r"(.+)", title)
            movie = m.group(1).strip().strip('"') if m else ""
            name = f"rt_{_slug(ev.get('event_ticker') or movie)}"
            if not movie or name in have:
                continue
            url = self.rt_lookup(movie)
            self.write({"kind": "rt_add", "event": ev.get("event_ticker"), "movie": movie,
                        "url": url})
            if url:
                self.sources.append(Source(name, url, key_rt,
                                           interval_weekday_hours(60, 120, 0, 24), ("KXRT",)))
                have.add(name)

    def rt_lookup(self, movie: str) -> str | None:
        try:
            r = self.s.get("https://www.rottentomatoes.com/search", params={"search": movie},
                           timeout=20)
        except requests.RequestException:
            return None
        if r.status_code != 200:
            return None
        m = re.search(r'href="(https://www\.rottentomatoes\.com/m/[a-z0-9_]+)"', r.text)
        return m.group(1) if m else None

    # -- uploads (finished hours only)
    def upload_done_hours(self, now: float) -> None:
        if not self.uploader:
            return
        cur = self._hour_file(now).name
        for p in sorted(self.out.glob("srclog-*.jsonl")):
            if p.name == cur or p.name in self.uploaded:
                continue
            try:
                self.uploader(f"srclog/{p.name}.gz", gzip.compress(p.read_bytes()))
                self.uploaded.add(p.name)
            except Exception as exc:  # noqa: BLE001
                log.warning("srclog upload %s: %s", p.name, exc)
                return

    def step(self) -> float:
        now = self.clock()
        if now >= self.rt_next:
            self.refresh_rt(now)
        now_et = datetime.fromtimestamp(now, ET)
        for src in list(self.sources):
            if now >= src.next_at:
                src.next_at = now + src.interval(now_et)
                self.poll(src, now)
        self.run_snaps(self.clock())
        nxt = min([s.next_at for s in self.sources] + [x[0] for x in self.snaps] + [now + 30])
        return max(0.5, nxt - self.clock())

    def run(self) -> None:
        last_up = 0.0
        self.write({"kind": "start", "sources": [s.name for s in self.sources]})
        while True:
            try:
                wait = self.step()
                if self.clock() - last_up > 300:
                    last_up = self.clock()
                    self.upload_done_hours(last_up)
            except Exception as exc:  # noqa: BLE001  never die; log and keep going
                log.warning("srclog step: %s", exc)
                wait = 10
            time.sleep(wait)


def github_put(token: str, repo: str = REPO, branch: str = BRANCH):
    """Writes one file to the data branch with the contents API (creates the branch once)."""
    api = f"https://api.github.com/repos/{repo}"
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})

    def put(path: str, data: bytes) -> None:
        if s.get(f"{api}/git/ref/heads/{branch}", timeout=20).status_code == 404:
            main = s.get(f"{api}/git/ref/heads/main", timeout=20).json()
            s.post(f"{api}/git/refs", json={"ref": f"refs/heads/{branch}",
                                            "sha": main["object"]["sha"]}, timeout=20)
        url = f"{api}/contents/{path}"
        g = s.get(url, params={"ref": branch}, timeout=20)
        body = {"message": f"srclog: {path} [skip ci]", "branch": branch,
                "content": base64.b64encode(data).decode()}
        if g.status_code == 200:
            body["sha"] = g.json().get("sha")
        r = s.put(url, json=body, timeout=60)
        r.raise_for_status()
    return put


def main() -> int:
    out = Path(os.environ.get("SRCLOG_DIR", "/var/lib/kalshi-srclog"))
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    for k in list(os.environ):          # shadow only: drop the trading key and other secrets
        if k.startswith(("KALSHI", "NTFY", "ANTHROPIC", "OANDA")):
            os.environ.pop(k, None)
    SrcLog(out, uploader=github_put(token) if token else None).run()
    return 0
