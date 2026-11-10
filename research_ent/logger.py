"""Box office + Rotten Tomatoes logger (read-only public data; never trades, never alerts).

Each run takes one snapshot and appends it to CSVs in OUT:
  kalshi_quotes.csv  every open Kalshi box-office / Rotten Tomatoes market: bid, ask, last, volume
  markets.csv        first sighting of each market: title, strikes, close time, rules text
  rt_scores.csv      Tomatometer score + review count (and audience score) for each RT market's title
  boxoffice.csv      Box Office Mojo daily chart rows for the last few days (gross, theaters, day #)
  probe.csv          HTTP status of each outside source (so a block shows up in the data)
  raw/               gzipped raw pages, a few per run, in case parsing needs fixing later

Settled markets with hourly candles (free from Kalshi) are pulled when BACKFILL=1, together with
Box Office Mojo daily charts back to BACKFILL_FROM, so a first box-office test needn't wait weeks.

usage: python research_ent/logger.py <out dir>
"""
from __future__ import annotations

import csv
import gzip
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

import requests

K = "https://api.elections.kalshi.com/trade-api/v2"
BOM = "https://www.boxofficemojo.com"
RT = "https://www.rottentomatoes.com"
# which Kalshi series count: matched on series ticker + title
SERIES_RE = re.compile(r"box ?office|opening weekend|domestic gross|rotten|tomatometer|^KXRT|^RT[A-Z0-9]|^KXBOX",
                       re.I)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")

S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
T0 = time.time()
NOW = datetime.now(timezone.utc).replace(microsecond=0)
STAMP = NOW.isoformat().replace("+00:00", "Z")
LOG: list[str] = []
PROBE: list[list] = []


def log(*a):
    msg = f"[{time.time() - T0:6.0f}s] " + " ".join(str(x) for x in a)
    print(msg, flush=True)
    LOG.append(msg)


def get(url, params=None, js=True, tries=4, source=""):
    status = None
    for i in range(tries):
        time.sleep(0.15)
        try:
            r = S.get(url, params=params, timeout=40)
        except requests.RequestException as e:
            status = f"err:{type(e).__name__}"
            time.sleep(2 * (i + 1))
            continue
        status = r.status_code
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(3 * (i + 1))
            continue
        if source:
            PROBE.append([STAMP, source, url[:200], r.status_code, len(r.content)])
        if not r.ok:
            log("HTTP", r.status_code, url[:120])
            return None
        try:
            return r.json() if js else r.text
        except ValueError:
            log("bad json", url[:120])
            return None
    if source:
        PROBE.append([STAMP, source, url[:200], status, 0])
    return None


def append(path: Path, header: list[str], rows: list[list]):
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(header)
        w.writerows(rows)


def save_raw(out: Path, name: str, text: str):
    d = out / "raw" / NOW.strftime("%Y%m%d")
    d.mkdir(parents=True, exist_ok=True)
    with gzip.open(d / f"{NOW.strftime('%H%M')}-{name}.html.gz", "wt", encoding="utf-8") as fh:
        fh.write(text)


def dollars(v):
    if v is None or v == "":
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v


# ------------------------------------------------------------------ Kalshi

def find_series() -> list[dict]:
    out, seen = [], set()
    for cat in ("Entertainment", "Culture", "Movies"):
        j = get(K + "/series", {"category": cat}) or {}
        for s in j.get("series") or []:
            tk = s.get("ticker") or ""
            if tk in seen:
                continue
            if SERIES_RE.search(tk) or SERIES_RE.search(s.get("title") or ""):
                seen.add(tk)
                out.append(s)
    log("series matched", len(out), ",".join(s["ticker"] for s in out)[:400])
    return out


def market_list(series: str, status: str | None) -> list[dict]:
    got, cur = [], None
    for _ in range(30):
        p = {"series_ticker": series, "limit": 1000}
        if status:
            p["status"] = status
        if cur:
            p["cursor"] = cur
        j = get(K + "/markets", p)
        if not j:
            break
        got += j.get("markets") or []
        cur = j.get("cursor")
        if not cur:
            break
    return got


def kind_of(series: dict, m: dict) -> str:
    text = " ".join([series.get("ticker", ""), series.get("title", ""), m.get("title") or "",
                     m.get("rules_primary") or ""])
    if re.search(r"rotten|tomatometer|^KXRT|^RT[A-Z0-9]", text, re.I):
        return "rt"
    return "box"


MARKET_COLS = ["first_seen", "ticker", "series", "event", "kind", "title", "sub", "strike_type", "floor",
               "cap", "open", "close", "rules", "rules2"]
QUOTE_COLS = ["ts", "ticker", "yes_bid", "yes_ask", "last", "volume", "open_interest", "status"]


def kalshi_snapshot(out: Path, series: list[dict]) -> list[dict]:
    known = set()
    mp = out / "markets.csv"
    if mp.exists():
        with mp.open(encoding="utf-8") as fh:
            known = {r["ticker"] for r in csv.DictReader(fh)}
    new_m, quotes, live = [], [], []
    for s in series:
        for m in market_list(s["ticker"], "open"):
            kind = kind_of(s, m)
            m["_kind"] = kind
            live.append(m)
            if m["ticker"] not in known:
                new_m.append([STAMP, m["ticker"], s["ticker"], m.get("event_ticker"), kind, m.get("title"),
                              m.get("yes_sub_title"), m.get("strike_type"), m.get("floor_strike"),
                              m.get("cap_strike"), m.get("open_time"), m.get("close_time"),
                              (m.get("rules_primary") or "")[:1500], (m.get("rules_secondary") or "")[:800]])
            quotes.append([STAMP, m["ticker"],
                           dollars(m.get("yes_bid_dollars", m.get("yes_bid"))),
                           dollars(m.get("yes_ask_dollars", m.get("yes_ask"))),
                           dollars(m.get("last_price_dollars", m.get("last_price"))),
                           m.get("volume_fp", m.get("volume")), m.get("open_interest_fp", m.get("open_interest")),
                           m.get("status")])
    append(mp, MARKET_COLS, new_m)
    append(out / "kalshi_quotes.csv", QUOTE_COLS, quotes)
    log("kalshi open markets", len(live), "new", len(new_m))
    return live


# ------------------------------------------------------------------ Rotten Tomatoes

def rt_targets(markets: list[dict]) -> dict[str, str]:
    """{event: title or RT url} for each RT event (one lookup per event, not per strike)."""
    out = {}
    for m in markets:
        if m.get("_kind") != "rt":
            continue
        ev = m.get("event_ticker") or m["ticker"]
        if ev in out:
            continue
        rules = (m.get("rules_primary") or "") + " " + (m.get("rules_secondary") or "")
        url = re.search(r"https?://(?:www\.)?rottentomatoes\.com/(?:m|tv)/[\w\-/]+", rules)
        if url:
            out[ev] = url.group(0).rstrip("/.")
            continue
        out[ev] = rt_title(m.get("title") or "")
    return out


def rt_title(title: str) -> str:
    """Film name out of a market title such as "Rotten Tomatoes score for Wicked?" or
    "Will Wicked's Rotten Tomatoes score be above 85%?"."""
    t = re.search(r"(?i)\b(?:for|of)\s+(.+?)\s*(?:\?|$|\s+be\b|\s+on rotten)", title)
    if not t:
        t = re.search(r"(?i)^(?:will|what will)?\s*(.+?)(?:'s|’s)?\s+(?:rotten tomatoes|rt|tomatometer)\b", title)
    name = t.group(1) if t else title
    name = re.sub(r"(?i)\b(?:rotten tomatoes|tomatometer|rt)\b|\bscore\b|\?", "", name)
    return name.strip(" '\"’")


def rt_parse(html: str) -> dict:
    d: dict = {}
    m = re.search(r'<script[^>]+id="media-scorecard-json"[^>]*>(.*?)</script>', html, re.S)
    if m:
        try:
            j = json.loads(m.group(1))
            cs, au = j.get("criticsScore") or {}, j.get("audienceScore") or {}
            d.update(critics=cs.get("score"), critics_n=cs.get("reviewCount") or cs.get("ratingCount"),
                     audience=au.get("score"), audience_n=au.get("reviewCount") or au.get("ratingCount"),
                     src="scorecard")
        except ValueError:
            pass
    if d.get("critics") in (None, ""):
        for blk in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S):
            try:
                j = json.loads(blk)
            except ValueError:
                continue
            ar = j.get("aggregateRating") if isinstance(j, dict) else None
            if ar:
                d.update(critics=ar.get("ratingValue"), critics_n=ar.get("reviewCount") or ar.get("ratingCount"),
                         src="ldjson")
                break
    t = re.search(r"<title>(.*?)</title>", html, re.S)
    d["page_title"] = unescape(t.group(1)).strip()[:120] if t else ""
    return d


def rt_find(title: str) -> str | None:
    html = get(f"{RT}/search", {"search": title}, js=False, source="rt_search")
    if not html:
        return None
    for path in re.findall(r'href="(https://www\.rottentomatoes\.com/(?:m|tv)/[\w\-/]+)"', html):
        return path
    return None


RT_COLS = ["ts", "event", "query", "url", "critics", "critics_n", "audience", "audience_n", "src", "page_title"]


def rt_snapshot(out: Path, markets: list[dict]):
    rows = []
    for i, (ev, q) in enumerate(rt_targets(markets).items()):
        url = q if q.startswith("http") else rt_find(q)
        if not url:
            rows.append([STAMP, ev, q, "", "", "", "", "", "notfound", ""])
            continue
        html = get(url, js=False, source="rt_page")
        if not html:
            rows.append([STAMP, ev, q, url, "", "", "", "", "blocked", ""])
            continue
        if i < 2 and NOW.hour % 6 == 0:
            save_raw(out, "rt-" + re.sub(r"\W", "", ev)[:40], html)
        d = rt_parse(html)
        rows.append([STAMP, ev, q, url, d.get("critics"), d.get("critics_n"), d.get("audience"),
                     d.get("audience_n"), d.get("src", "noparse"), d.get("page_title")])
    append(out / "rt_scores.csv", RT_COLS, rows)
    log("rt rows", len(rows), "parsed", sum(1 for r in rows if r[4] not in ("", None)))


# ------------------------------------------------------------------ Box Office Mojo

class Table(HTMLParser):
    """Rows of the first <table> on the page, cell text plus the first link in each cell."""

    def __init__(self):
        super().__init__()
        self.rows, self.row, self.cell, self.href = [], None, None, None
        self.depth, self.done = 0, False

    def handle_starttag(self, tag, attrs):
        if self.done:
            return
        if tag == "table":
            self.depth += 1
        elif self.depth and tag == "tr":
            self.row = []
        elif self.depth and tag in ("td", "th") and self.row is not None:
            self.cell, self.href = [], None
        elif tag == "a" and self.cell is not None and self.href is None:
            self.href = dict(attrs).get("href")

    def handle_endtag(self, tag):
        if self.done:
            return
        if tag in ("td", "th") and self.cell is not None and self.row is not None:
            self.row.append(("".join(self.cell).strip(), self.href or ""))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            if self.row:
                self.rows.append(self.row)
            self.row = None
        elif tag == "table" and self.depth:
            self.depth -= 1
            if not self.depth:
                self.done = True

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)


def num(s):
    s = (s or "").replace("$", "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


BOX_COLS = ["fetched", "day", "rank", "release", "release_id", "gross", "theaters", "per_theater", "to_date",
            "days_in_release", "distributor"]


def bom_day(day: date) -> tuple[list[list], str | None]:
    html = get(f"{BOM}/date/{day.isoformat()}/", js=False, source="bom_daily")
    if not html:
        return [], None
    t = Table()
    t.feed(html)
    if not t.rows:
        return [], html
    head = [c[0].lower() for c in t.rows[0]]

    def col(name):
        for i, h in enumerate(head):
            if name in h:
                return i
        return None

    ix = {k: col(k) for k in ("rank", "release", "daily", "theaters", "avg", "to date", "days", "distributor")}
    rows = []
    for r in t.rows[1:]:
        def cell(k, link=False):
            i = ix.get(k)
            if i is None or i >= len(r):
                return ""
            return r[i][1] if link else r[i][0]
        rel_link = cell("release", link=True)
        rid = re.search(r"/release/(rl\d+)", rel_link)
        rows.append([STAMP, day.isoformat(), cell("rank"), cell("release"), rid.group(1) if rid else "",
                     num(cell("daily")), num(cell("theaters")), num(cell("avg")), num(cell("to date")),
                     cell("days"), cell("distributor")])
    return rows, html


def box_snapshot(out: Path, days_back: int = 3):
    rows = []
    today = NOW.date()
    for k in range(1, days_back + 1):
        d = today - timedelta(days=k)
        r, html = bom_day(d)
        rows += r
        if html and k == 1 and NOW.hour % 6 == 0:
            save_raw(out, f"bom-{d.isoformat()}", html)
    append(out / "boxoffice.csv", BOX_COLS, rows)
    log("box office rows", len(rows))


# ------------------------------------------------------------------ backfill (one-off)

def px(o):
    v = o.get("close_dollars")
    if v is None:
        v = o.get("close")
        try:
            v = float(v)
        except (TypeError, ValueError):
            return None
        return v / 100 if v > 1.0 else v
    return v


def ts(s):
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()) if s else 0


def backfill(out: Path, series: list[dict]):
    bdir = out / "backfill"
    bdir.mkdir(parents=True, exist_ok=True)
    mrows, crows = [], []
    earliest = None
    for s in series:
        got = {}
        for path, extra, hist in (("/markets", {"status": "settled"}, 0), ("/historical/markets", {}, 1)):
            cur = None
            for _ in range(40):
                p = {"series_ticker": s["ticker"], "limit": 1000, **extra}
                if cur:
                    p["cursor"] = cur
                j = get(K + path, p)
                if not j:
                    break
                for m in j.get("markets") or []:
                    got.setdefault(m["ticker"], (m, hist))
                cur = j.get("cursor")
                if not cur:
                    break
        for tk, (m, hist) in got.items():
            if m.get("result") not in ("yes", "no"):
                continue
            kind = kind_of(s, m)
            mrows.append([tk, s["ticker"], m.get("event_ticker"), kind, m.get("title"), m.get("yes_sub_title"),
                          m.get("strike_type"), m.get("floor_strike"), m.get("cap_strike"), m.get("open_time"),
                          m.get("close_time"), m.get("result"), m.get("volume_fp", m.get("volume")),
                          (m.get("rules_primary") or "")[:1500], hist])
            end = ts(m.get("close_time"))
            if kind == "box" and end:
                d = datetime.fromtimestamp(end, timezone.utc).date()
                earliest = d if earliest is None or d < earliest else earliest
            start = max(ts(m.get("open_time")), end - 10 * 86400)
            url = (f"{K}/historical/markets/{tk}/candlesticks" if hist
                   else f"{K}/series/{s['ticker']}/markets/{tk}/candlesticks")
            j = get(url, {"start_ts": start, "end_ts": end, "period_interval": 60})
            for c in (j or {}).get("candlesticks") or []:
                yb, ya = c.get("yes_bid") or {}, c.get("yes_ask") or {}
                crows.append([tk, c.get("end_period_ts"), px(yb), px(ya), px(c.get("price") or {}),
                              c.get("volume_fp", c.get("volume"))])
        log("backfill", s["ticker"], "settled", len(got))
    with gzip.open(bdir / "settled_markets.csv.gz", "wt", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "series", "event", "kind", "title", "sub", "strike_type", "floor", "cap", "open",
                    "close", "result", "volume", "rules", "hist"])
        w.writerows(mrows)
    with gzip.open(bdir / "candles.csv.gz", "wt", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "end_ts", "yes_bid", "yes_ask", "last", "volume"])
        w.writerows(crows)
    log("backfill markets", len(mrows), "candles", len(crows))
    # daily box office for every day the settled box-office markets could depend on
    frm = os.environ.get("BACKFILL_FROM")
    d0 = date.fromisoformat(frm) if frm else (earliest - timedelta(days=14) if earliest else None)
    if not d0:
        return
    rows, d = [], d0
    while d < NOW.date() and time.time() - T0 < 60 * 40:
        r, _ = bom_day(d)
        rows += r
        d += timedelta(days=1)
    with gzip.open(bdir / "boxoffice_daily.csv.gz", "wt", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(BOX_COLS)
        w.writerows(rows)
    log("backfill box office days", (d - d0).days, "rows", len(rows))


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
    out.mkdir(parents=True, exist_ok=True)
    series = find_series()
    live = kalshi_snapshot(out, series)
    for step in (lambda: rt_snapshot(out, live), lambda: box_snapshot(out)):
        try:
            step()
        except Exception as e:  # one broken source must not lose the others
            log("step failed", type(e).__name__, e)
    if os.environ.get("BACKFILL") == "1":
        backfill(out, series)
    append(out / "probe.csv", ["ts", "source", "url", "status", "bytes"], PROBE)
    with (out / "log.txt").open("a", encoding="utf-8") as fh:
        fh.write(f"--- run {STAMP}\n" + "\n".join(LOG) + "\n")


if __name__ == "__main__":
    main()
