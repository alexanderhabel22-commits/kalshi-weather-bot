"""Fast quake YES: buy YES on Kalshi's earthquake markets right after a big quake (Nov 21, 2026).

Thread "Paper test fast quake YES bot". Alex, Nov 20 9:10 PM ET: "Understood but still want to
test. How is it possible they are getting the data faster". Built PAPER only (PR #339); then
Alex, Nov 20 9:25 PM ET: "Let's just make it live with money!"

REAL MONEY (small test), switched by quakefast_control.json on main (mode live | off), read every
5 min; it also obeys the global kill switch (data/trader_state.json mode off/paper or halted) and
goes off by itself when either file can't be read for 30 min. Rails (control file, defaults
below): 1 contract per order, only when GFZ or EMSC show a magnitude at least 0.2 above the
strike, ask at most 60c with at least 1 contract on offer, immediate-or-cancel at the ask (never
rests, never chases), one real order per market, exchange shard 0 markets only, and a HARD $3 cap:
-(settled real P&L) + cost of open real bets + the new order <= $3. When the cap leaves no room for
another order: one ntfy push, then no more real orders. Every paper bet keeps logging alongside
(paper twin). Review at 10 real fills.

Why: on Nov 20 (7:09 PM ET) a M6.7 hit Peru. Kalshi's "biggest quake today >= 6.6" YES traded
13-33c for minutes before USGS (the settlement source) posted it at ~7 min, then jumped to
93-95c. Someone with fast data bought YES cheap. GFZ had it at ~3.5 min, EMSC around then too.
Question this logger answers with real timestamps: if we hear about a big quake from the fastest
free feeds, is the YES ask still cheap enough to pay off after early-magnitude revisions and the
fee, and how far behind the fast money are we?

What it does (always-on service on the kalshi-m15 droplet, a few hundred KB a minute):
  - polls free quake feeds every 10-30 s (USGS realtime feed, GFZ GEOFON, EMSC fdsn + EMSC's
    push websocket, Pacific Tsunami Warning Center and NTWC bulletins, Japan JMA, Indonesia
    BMKG, New Zealand GeoNet) and logs when each source FIRST shows each M5.5+ quake and every
    magnitude revision after that;
  - polls Kalshi's public quake markets every 15 s and logs every quote change, so we can see
    when Kalshi moved relative to each source even if we never saw the quake;
  - when any source shows M5.8+, it snapshots every open quake market (bid/ask + depth at the
    ask) right then, records a 1-contract PAPER YES buy at the ask on each strike that source's
    magnitude clears (two variants: clears by 0.0 and by 0.2, the second allowing for
    revisions), and snapshots again at +15 s ... +20 min;
  - 30 min after a quake it saves Kalshi's trade tape for those markets (first trades, first
    50c+ print), which pins the fast money's real reaction time;
  - paper fills settle from Kalshi's own result; P&L = payout - ask - taker fee.
Outputs: hourly logs quakefast-YYYY-MM-DD-HH.jsonl on branch quakefast-data, and a status file
m15/quakefast.json on branch m15-data (dashboard TESTS tab).

Honest odds (see /mnt/project-files/analysis/quake-fast/): fast traders already move Kalshi 1-4
min after a quake, often before GFZ; first magnitudes are off by 0.1-0.3 (Peru: GFZ 6.66, EMSC
6.8, PTWC 6.9, USGS 6.7); asks near the strike are thin. Most likely result: we are too late
or the revision risk eats the edge. This test exists to measure that, cheaply.

Run: python -m wxbot.quakefast  (systemd unit deploy/m15/kalshi-quakefast.service)
"""
from __future__ import annotations

import gzip
import json
import logging
import math
import os
import queue
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import requests

from .kalshi_api import OrderRejected, fill_qty

log = logging.getLogger("wxbot")
UTC = timezone.utc
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
UA = "kalshi-quakefast/1 (paper research logger)"

LOG_MIN = 5.5             # track every quake at least this big (latency + revisions)
TRIGGER_MIN = 5.8         # snapshot Kalshi + paper fills from this magnitude on
MARGINS = (0.0, 0.2)      # paper YES when source magnitude >= strike + margin
MAX_ASK = 0.95            # never "buy" above this
SNAP_DELAYS = (15, 30, 60, 120, 300, 600, 1200)
TAPE_DELAY_S = 1800
MATCH_KM = 400
MATCH_S = 180
FRESH_S = 1800            # paper trades only on quakes less than 30 min old
QUOTE_SERIES = ("KXBIGGESTQUAKE", "KXBIGGESTQUAKEH")
OTHER_SERIES = ("KXBIGGESTQUAKEMONTH", "KXEARTHQUAKE", "KXEARTHQUAKEM", "KXEARTHQUAKEM7",
                "KXEARTHQUAKEJAPAN", "KXEARTHQUAKECALIFORNIA", "KXEARTHQUAKELA", "KXTSUNAMI")
KEEP_QUAKES_S = 3 * 86400

REPO = "alexanderhabel22-commits/kalshi-weather-bot"
CONTROL_URL = f"https://raw.githubusercontent.com/{REPO}/main/quakefast_control.json"
SWITCH_URL = f"https://raw.githubusercontent.com/{REPO}/main/data/trader_state.json"
CONTROL_STALE_S = 1800
REAL_DEFAULTS = {"mode": "off", "contracts": 1, "max_loss_usd": 3.0, "margin": 0.2,
                 "max_ask": 0.60, "min_depth": 1.0, "sources": ["gfz", "gfz_fdsn", "emsc", "emsc_ws"]}
REAL_MAX_CONTRACTS = 2    # code ceiling whatever the control file says


# ------------------------------------------------------------------ small helpers

def fee1(q: float) -> float:
    """Kalshi taker fee on one contract at price q, rounded up to the cent."""
    return math.ceil(0.07 * q * (1 - q) * 100 - 1e-9) / 100


def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _iso(s: str | None) -> float | None:
    """ISO time (with Z, offset, or naive = UTC) -> epoch seconds."""
    if not s:
        return None
    s = s.strip().replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=UTC)
    return d.timestamp()


def km(lat1, lon1, lat2, lon2) -> float:
    if None in (lat1, lon1, lat2, lon2):
        return 0.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371 * 2 * math.asin(min(1.0, math.sqrt(a)))


def px(m: dict, k: str) -> float | None:
    """Kalshi market price field in dollars (accepts *_dollars strings or cents)."""
    v = _f(m.get(k + "_dollars", m.get(k)))
    if v is None:
        return None
    return round(v / 100, 4) if v > 1.5 else round(v, 4)


def et(ts: float) -> str:
    from zoneinfo import ZoneInfo
    return datetime.fromtimestamp(ts, ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class Det:
    """One source's view of one quake."""
    src: str
    sid: str
    t: float | None        # origin time (epoch); None when the source doesn't give one (PTWC)
    lat: float | None
    lon: float | None
    mag: float
    place: str = ""
    magtype: str = ""
    issued: float | None = None   # bulletin time, for feeds without an origin time


# ------------------------------------------------------------------ feed parsers (pure)

def parse_usgs(js: dict) -> list[Det]:
    out = []
    for f in (js or {}).get("features") or []:
        p, g = f.get("properties") or {}, (f.get("geometry") or {}).get("coordinates") or [None, None]
        m = _f(p.get("mag"))
        if m is None or (p.get("type") or "earthquake") != "earthquake":
            continue
        t = _f(p.get("time"))
        out.append(Det("usgs", str(f.get("id")), t / 1000 if t else None, _f(g[1]), _f(g[0]), m,
                       p.get("place") or "", p.get("magType") or ""))
    return out


_GFZ_ITEM = re.compile(r"<item>(.*?)</item>", re.S)


def parse_gfz_rss(text: str) -> list[Det]:
    out = []
    for it in _GFZ_ITEM.findall(text or ""):
        title = re.search(r"<title>\s*M\s*([\d.]+),\s*(.*?)</title>", it, re.S)
        desc = re.search(r"<description>\s*(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\s+(-?[\d.]+)\s+(-?[\d.]+)", it)
        guid = re.search(r"<guid[^>]*>(.*?)</guid>", it)
        if not (title and desc and guid):
            continue
        out.append(Det("gfz", guid.group(1).strip(), _iso(desc.group(1).replace(" ", "T")),
                       _f(desc.group(2)), _f(desc.group(3)), float(title.group(1)),
                       title.group(2).strip()))
    return out


def parse_gfz_text(text: str) -> list[Det]:
    out = []
    for line in (text or "").splitlines():
        if line.startswith("#") or "|" not in line:
            continue
        c = line.split("|")
        if len(c) < 13 or _f(c[10]) is None:
            continue
        out.append(Det("gfz_fdsn", c[0], _iso(c[1]), _f(c[2]), _f(c[3]), float(c[10]), c[12], c[9]))
    return out


def _emsc_feature(f: dict, src: str) -> Det | None:
    p = f.get("properties") or {}
    m = _f(p.get("mag"))
    if m is None:
        return None
    return Det(src, str(p.get("unid") or f.get("id")), _iso(p.get("time")), _f(p.get("lat")),
               _f(p.get("lon")), m, p.get("flynn_region") or "", p.get("magtype") or "")


def parse_emsc(js: dict) -> list[Det]:
    return [d for d in (_emsc_feature(f, "emsc") for f in (js or {}).get("features") or []) if d]


def parse_emsc_ws(msg: str) -> list[Det]:
    try:
        js = json.loads(msg)
    except ValueError:
        return []
    d = _emsc_feature(js.get("data") or {}, "emsc_ws")
    return [d] if d else []


_ATOM_ENTRY = re.compile(r"<entry>(.*?)</entry>", re.S)


def parse_tsunami_atom(text: str, src: str) -> list[Det]:
    """PTWC/NTWC Atom feed. No origin time in the feed: matched to quakes by place and recency."""
    out = []
    for e in _ATOM_ENTRY.findall(text or ""):
        mag = re.search(r"Preliminary Magnitude:\s*</strong>\s*([\d.]+)\s*\(?(\w*)", e)
        if not mag:
            continue
        lat = re.search(r"<geo:lat>\s*(-?[\d.]+)", e)
        lon = re.search(r"<geo:long>\s*(-?[\d.]+)", e)
        title = re.search(r"<title>(.*?)</title>", e, re.S)
        upd = re.search(r"<updated>(.*?)</updated>", e)
        link = re.search(r'href="(https://www\.tsunami\.gov/events/[^"]+?)/\d+/\w+/', e)
        uid = re.search(r"<id>(.*?)</id>", e)
        sid = link.group(1) if link else (uid.group(1) if uid else "")
        out.append(Det(src, sid, None, _f(lat.group(1)) if lat else None,
                       _f(lon.group(1)) if lon else None, float(mag.group(1)),
                       (title.group(1).strip() if title else ""), mag.group(2),
                       _iso(upd.group(1)) if upd else None))
    return out


def parse_jma(js: list) -> list[Det]:
    """Japan Meteorological Agency list.json (JMA magnitude; Japan and nearby only)."""
    out, seen = [], set()
    for e in (js or [])[:80]:
        m = _f(e.get("mag"))
        if m is None or e.get("eid") in seen:
            continue
        seen.add(e.get("eid"))
        cod = re.match(r"([+-][\d.]+)([+-][\d.]+)", e.get("cod") or "")
        out.append(Det("jma", str(e.get("eid")), _iso(e.get("at")),
                       _f(cod.group(1)) if cod else None, _f(cod.group(2)) if cod else None, m,
                       e.get("en_anm") or e.get("anm") or "", "Mj"))
    return out


def parse_bmkg(js: dict) -> list[Det]:
    g = ((js or {}).get("Infogempa") or {}).get("gempa") or []
    if isinstance(g, dict):
        g = [g]
    out = []
    for e in g:
        m = _f(e.get("Magnitude"))
        c = (e.get("Coordinates") or "").split(",")
        if m is None:
            continue
        out.append(Det("bmkg", str(e.get("DateTime")), _iso(e.get("DateTime")),
                       _f(c[0]) if len(c) == 2 else None, _f(c[1]) if len(c) == 2 else None, m,
                       e.get("Wilayah") or ""))
    return out


def parse_geonet(js: dict) -> list[Det]:
    out = []
    for f in (js or {}).get("features") or []:
        p, g = f.get("properties") or {}, (f.get("geometry") or {}).get("coordinates") or [None, None]
        m = _f(p.get("magnitude"))
        if m is None:
            continue
        out.append(Det("geonet", str(p.get("publicID")), _iso(p.get("time")), _f(g[1]), _f(g[0]),
                       round(m, 2), p.get("locality") or ""))
    return out


@dataclass
class Feed:
    name: str
    url: str
    parse: Callable
    interval: float
    kind: str = "json"          # json | text
    params: dict | None = None  # callable params get evaluated each poll
    etag: str | None = None
    lm: str | None = None


def default_feeds() -> list[Feed]:
    def since(h=6):
        return lambda: {"format": "json", "minmag": LOG_MIN,
                        "starttime": (datetime.now(UTC) - timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%S")}

    def since_text(h=6):
        return lambda: {"format": "text", "minmag": LOG_MIN,
                        "starttime": (datetime.now(UTC) - timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%S")}
    return [
        Feed("usgs", "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_hour.geojson",
             parse_usgs, 10),
        Feed("gfz", "https://geofon.gfz.de/eqinfo/list.php", parse_gfz_rss, 10, "text",
             {"fmt": "rss"}),
        Feed("gfz_fdsn", "https://geofon.gfz.de/fdsnws/event/1/query", parse_gfz_text, 30, "text",
             since_text()),
        Feed("emsc", "https://www.seismicportal.eu/fdsnws/event/1/query", parse_emsc, 15, "json",
             since()),
        Feed("ptwc", "https://www.tsunami.gov/events/xml/PHEBAtom.xml",
             lambda t: parse_tsunami_atom(t, "ptwc"), 15, "text"),
        Feed("ntwc", "https://www.tsunami.gov/events/xml/PAAQAtom.xml",
             lambda t: parse_tsunami_atom(t, "ntwc"), 15, "text"),
        Feed("jma", "https://www.jma.go.jp/bosai/quake/data/list.json", parse_jma, 30),
        Feed("bmkg", "https://data.bmkg.go.id/DataMKG/TEWS/gempaterkini.json", parse_bmkg, 20),
        Feed("geonet", "https://api.geonet.org.nz/quake", parse_geonet, 30, "json", {"MMI": 4}),
    ]


EMSC_WS = "wss://www.seismicportal.eu/standing_order/websocket"


# ------------------------------------------------------------------ quakes (cross-source)

@dataclass
class Quake:
    qid: str
    t: float | None
    lat: float | None
    lon: float | None
    place: str
    first_ts: float
    srcs: dict = field(default_factory=dict)     # src -> {sid, first_ts, first_mag, mag, max_mag, revs}
    triggered: bool = False
    tape_done: bool = False
    kmove: dict | None = None
    seeded: bool = False       # first seen on a feed's start-up poll: logged, never traded

    def best_mag(self) -> float:
        return max((s["mag"] for s in self.srcs.values()), default=0.0)

    def to_json(self) -> dict:
        return {"qid": self.qid, "t": self.t, "lat": self.lat, "lon": self.lon, "place": self.place,
                "first_ts": self.first_ts, "srcs": self.srcs, "triggered": self.triggered,
                "tape_done": self.tape_done, "kmove": self.kmove, "seeded": self.seeded}

    @classmethod
    def from_json(cls, d: dict) -> "Quake":
        return cls(d["qid"], d.get("t"), d.get("lat"), d.get("lon"), d.get("place", ""),
                   d["first_ts"], d.get("srcs") or {}, d.get("triggered", False),
                   d.get("tape_done", False), d.get("kmove"), d.get("seeded", False))


def match(quakes: list[Quake], d: Det, now: float) -> Quake | None:
    best, bd = None, 1e9
    for q in quakes:
        dist = km(q.lat, q.lon, d.lat, d.lon)
        if dist > MATCH_KM:
            continue
        if d.t is not None and q.t is not None:
            if abs(d.t - q.t) > MATCH_S:
                continue
        elif now - (q.t or q.first_ts) > 3600:        # tsunami bulletin: only quakes from the last hour
            continue
        if dist < bd:
            best, bd = q, dist
    return best


# ------------------------------------------------------------------ Kalshi market helpers

def strike(m: dict) -> float | None:
    v = _f(m.get("floor_strike"))
    if v is None:
        v = _f((m.get("ticker") or "").rsplit("-", 1)[-1].lstrip("T"))
    return round(v, 2) if v is not None else None


def covers(m: dict, t: float) -> bool:
    """Is origin time t inside this market's window? Daily KXBIGGESTQUAKE-21NOV26 = UTC day;
    hourly KXBIGGESTQUAKEH-26NOV2115 = that UTC hour (if the ticker parses); others: open until close."""
    tk = m.get("ticker") or ""
    part = tk.split("-")[1] if tk.count("-") >= 1 else ""
    if tk.startswith("KXBIGGESTQUAKE-"):
        try:
            d = datetime.strptime(part, "%d%b%y").replace(tzinfo=UTC).timestamp()
        except ValueError:
            return False
        return d <= t < d + 86400
    if tk.startswith("KXBIGGESTQUAKEH-"):
        for fmt in ("%y%b%d%H", "%d%b%y%H"):
            try:
                d = datetime.strptime(part, fmt).replace(tzinfo=UTC).timestamp()
                return d <= t < d + 3600 or d - 3600 <= t < d   # ticker hour may be the end hour
            except ValueError:
                continue
        ct = _iso(m.get("close_time"))
        return ct is not None and ct - 3600 <= t < ct
    ct = _iso(m.get("close_time"))
    ot = _iso(m.get("open_time"))
    return (ct is None or t < ct) and (ot is None or t >= ot - 86400 * 40)


def magnitude_market(m: dict) -> bool:
    """Only 'biggest quake >= X' style markets get paper fills (strike = magnitude)."""
    x = strike(m)
    return x is not None and 4.0 <= x <= 9.9 and (m.get("ticker") or "").startswith("KXBIGGESTQUAKE")


def ask_depth(book: dict) -> tuple[float | None, float | None]:
    """Best YES ask and its size from a Kalshi orderbook (YES ask = 1 - best NO bid)."""
    ob = (book or {}).get("orderbook_fp") or (book or {}).get("orderbook") or {}
    no = ob.get("no_dollars") or ob.get("no") or []
    best = None
    for p, q in no:
        p, q = _f(p), _f(q)
        if p is None or q is None:
            continue
        p = p / 100 if p > 1.5 else p
        if best is None or p > best[0]:
            best = (p, q)
    if not best:
        return None, None
    return round(1 - best[0], 4), best[1]


# ------------------------------------------------------------------ the logger

class QuakeFast:
    def __init__(self, out_dir: Path, feeds: list[Feed] | None = None, clock=time.time,
                 session: requests.Session | None = None, uploader=None, status_up=None,
                 ws: bool = True, client=None, alert=None):
        self.out = out_dir
        self.out.mkdir(parents=True, exist_ok=True)
        self.feeds = feeds if feeds is not None else default_feeds()
        self.clock = clock
        self.s = session or requests.Session()
        self.s.headers.update({"User-Agent": UA})
        self.uploader = uploader          # (path, bytes) -> None   hourly logs
        self.status_up = status_up        # ({name: text}) -> None  m15-data status
        self.ws = ws
        self.q: queue.Queue = queue.Queue()
        self.quakes: list[Quake] = []
        self.fills: list[dict] = []
        self.snaps: list[tuple[float, str, int]] = []   # (due, qid, delay)
        self.quotes: dict[str, tuple] = {}               # ticker -> (bid, ask, last)
        self.qhist: list[tuple[float, str, float | None, float | None]] = []   # (ts, ticker, bid, ask)
        self.health: dict[str, dict] = {}
        self.warm: set[str] = set()                      # feeds past their first (seed) poll
        self.uploaded: set[str] = set()
        self._lock = threading.Lock()
        self.client = client              # KalshiClient (real orders) or None = paper only
        self.alert = alert                # (title, body) -> None  ntfy push
        self.real: list[dict] = []        # real orders (filled or not)
        self.rcfg = dict(REAL_DEFAULTS)
        self.switch = {"mode": None, "halted": False}
        self.ok_at = -1e18
        self.cap_alerted = False
        self.state_file = self.out / "state.json"
        self.load()

    # -- files / state
    def _hour_file(self, ts: float) -> Path:
        return self.out / f"quakefast-{datetime.fromtimestamp(ts, UTC):%Y-%m-%d-%H}.jsonl"

    def write(self, row: dict) -> None:
        row.setdefault("ts", round(self.clock(), 3))
        row["et"] = et(row["ts"])
        with self._lock, open(self._hour_file(row["ts"]), "a") as fh:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")

    def load(self) -> None:
        try:
            js = json.loads(self.state_file.read_text())
        except (OSError, ValueError):
            return
        self.quakes = [Quake.from_json(d) for d in js.get("quakes") or []]
        self.fills = js.get("fills") or []
        self.uploaded = set(js.get("uploaded") or [])
        self.real = js.get("real") or []
        self.cap_alerted = bool(js.get("cap_alerted"))

    def save(self) -> None:
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps({"quakes": [q.to_json() for q in self.quakes], "fills": self.fills,
                                   "uploaded": sorted(self.uploaded)[-200:], "real": self.real,
                                   "cap_alerted": self.cap_alerted}, separators=(",", ":")))
        tmp.replace(self.state_file)

    # -- feeds
    def fetch(self, f: Feed, s: requests.Session):
        params = f.params() if callable(f.params) else f.params
        hdr = {}
        if f.etag:
            hdr["If-None-Match"] = f.etag
        if f.lm:
            hdr["If-Modified-Since"] = f.lm
        r = s.get(f.url, params=params, headers=hdr, timeout=8)
        if r.status_code == 304:
            return None, 304, 0
        r.raise_for_status()
        f.etag, f.lm = r.headers.get("etag"), r.headers.get("last-modified")
        body = r.text if f.kind == "text" else r.json()
        return f.parse(body), 200, len(r.content)

    def feed_loop(self, f: Feed, stop: threading.Event) -> None:
        s = requests.Session()
        s.headers.update({"User-Agent": UA})
        while not stop.is_set():
            t0 = self.clock()
            try:
                dets, status, n = self.fetch(f, s)
                self.q.put(("feed", f.name, t0, dets, status, round(self.clock() - t0, 3), None))
            except Exception as exc:  # noqa: BLE001
                self.q.put(("feed", f.name, t0, None, 0, round(self.clock() - t0, 3), str(exc)[:120]))
            stop.wait(max(1.0, f.interval - (self.clock() - t0)))

    def ws_loop(self, stop: threading.Event) -> None:
        try:
            from websockets.sync.client import connect
        except ImportError:
            log.warning("quakefast: websockets missing, EMSC push feed off")
            return
        while not stop.is_set():
            try:
                with connect(EMSC_WS, open_timeout=20, close_timeout=5) as w:
                    self.q.put(("feed", "emsc_ws", self.clock(), [], 200, 0, None))
                    while not stop.is_set():
                        try:
                            msg = w.recv(timeout=60)
                        except TimeoutError:
                            continue
                        self.q.put(("feed", "emsc_ws", self.clock(), parse_emsc_ws(msg), 200, 0, None))
            except Exception as exc:  # noqa: BLE001
                self.q.put(("feed", "emsc_ws", self.clock(), None, 0, 0, str(exc)[:120]))
                stop.wait(30)

    def on_feed(self, name: str, ts: float, dets, status: int, lat: float, err: str | None) -> None:
        h = self.health.setdefault(name, {"ok": 0, "err": 0, "last_ok": None, "last_err": None})
        if err or dets is None and status != 304:
            h["err"] += 1
            if h.get("last_err_msg") != err:
                self.write({"kind": "feed_err", "src": name, "err": err})
            h["last_err"], h["last_err_msg"] = ts, err
            return
        h["ok"] += 1
        h["last_ok"], h["lat"] = ts, lat
        seed = name not in self.warm
        if status == 200:
            self.warm.add(name)
        for d in dets or []:
            if d.mag >= LOG_MIN:
                self.on_det(d, ts, seed)

    def on_det(self, d: Det, ts: float, seed: bool = False) -> None:
        if d.t is not None and ts - d.t > 6 * 3600:
            return                                    # old news
        if d.t is None and d.issued is not None and ts - d.issued > 3 * 3600:
            return                                    # old tsunami bulletin
        q = match(self.quakes, d, ts)
        if q is None:
            q = Quake(f"q{int(ts)}-{d.src}", d.t, d.lat, d.lon, d.place, ts, seeded=seed)
            self.quakes.append(q)
            self.write({"kind": "quake", "qid": q.qid, "src": d.src, "t": d.t, "lat": d.lat,
                        "lon": d.lon, "mag": d.mag, "place": d.place})
        if q.t is None and d.t is not None:
            q.t = d.t
        s = q.srcs.get(d.src)
        if s is None:
            s = q.srcs[d.src] = {"sid": d.sid, "first_ts": round(ts, 1), "first_mag": d.mag,
                                 "mag": d.mag, "max_mag": d.mag, "magtype": d.magtype, "revs": []}
            if seed:
                s["seed"] = True                      # we started after this source had it
            lag = round(ts - q.t, 1) if q.t and not seed else None
            self.write({"kind": "seen", "qid": q.qid, "src": d.src, "sid": d.sid, "mag": d.mag,
                        "magtype": d.magtype, "origin": d.t, "lag_s": lag, "place": d.place,
                        "seed": seed})
            self.maybe_trade(q, d.src, d.mag, ts)
        elif abs(d.mag - s["mag"]) >= 0.005:
            s["revs"].append([round(ts, 1), d.mag])
            s["revs"] = s["revs"][-20:]
            prev, s["mag"] = s["mag"], d.mag
            s["max_mag"] = max(s["max_mag"], d.mag)
            self.write({"kind": "rev", "qid": q.qid, "src": d.src, "mag": d.mag, "prev": prev})
            if d.mag > prev:
                self.maybe_trade(q, d.src, d.mag, ts)

    # -- Kalshi (public market data only)
    def markets(self, series: str) -> list[dict]:
        r = self.s.get(f"{KALSHI}/markets", params={"series_ticker": series, "status": "open",
                                                   "limit": 200}, timeout=10)
        r.raise_for_status()
        return r.json().get("markets") or []

    def book(self, ticker: str) -> dict:
        r = self.s.get(f"{KALSHI}/markets/{ticker}/orderbook", timeout=10)
        r.raise_for_status()
        return r.json()

    def poll_quotes(self, now: float) -> None:
        for se in QUOTE_SERIES:
            try:
                ms = self.markets(se)
            except Exception as exc:  # noqa: BLE001
                self.health.setdefault("kalshi", {"ok": 0, "err": 0})["err"] += 1
                log.info("quakefast kalshi %s: %s", se, exc)
                continue
            self.health.setdefault("kalshi", {"ok": 0, "err": 0})["ok"] += 1
            self.health["kalshi"]["last_ok"] = now
            for m in ms:
                tk = m.get("ticker")
                v = (px(m, "yes_bid"), px(m, "yes_ask"), px(m, "last_price"))
                if self.quotes.get(tk) != v:
                    old = self.quotes.get(tk)
                    self.quotes[tk] = v
                    self.qhist.append((now, tk, v[0], v[1]))
                    self.write({"kind": "quote", "ticker": tk, "bid": v[0], "ask": v[1], "last": v[2],
                                "vol": m.get("volume_fp", m.get("volume")),
                                "prev": list(old) if old else None})
        self.qhist = [x for x in self.qhist if now - x[0] < 7200]
        self.check_kmove(now)

    def check_kmove(self, now: float) -> None:
        """First time Kalshi's YES bid jumped 10c+ above its pre-quake level on a strike the quake
        could reach (strike <= best magnitude + 0.3), measured from origin time."""
        for q in self.quakes:
            if q.kmove or q.seeded or not q.t or now - q.t > 3600 or q.best_mag() < TRIGGER_MIN:
                continue
            for tk in {x[1] for x in self.qhist}:
                x = _f(tk.rsplit("-", 1)[-1])
                if x is None or x > q.best_mag() + 0.3 or not covers({"ticker": tk}, q.t):
                    continue
                before = [b for ts, t2, b, _ in self.qhist if t2 == tk and ts <= q.t and b is not None]
                base = before[-1] if before else None
                for ts, t2, b, _ in self.qhist:
                    if t2 != tk or ts <= q.t or b is None or base is None:
                        continue
                    if b >= base + 0.10:
                        if not q.kmove or ts < q.kmove["ts"]:
                            q.kmove = {"ts": round(ts, 1), "lag_s": round(ts - q.t, 1), "ticker": tk,
                                       "from": base, "to": b}
                        break
            if q.kmove:
                self.write({"kind": "kmove", "qid": q.qid, **q.kmove})

    def all_markets(self) -> list[dict]:
        ms = []
        for se in QUOTE_SERIES + OTHER_SERIES:
            try:
                ms += self.markets(se)
            except Exception as exc:  # noqa: BLE001
                log.info("quakefast kalshi %s: %s", se, exc)
        return ms

    def snapshot(self, q: Quake, label: str, ms: list[dict] | None = None) -> list[dict]:
        ms = self.all_markets() if ms is None else ms
        rows = [[m.get("ticker"), px(m, "yes_bid"), px(m, "yes_ask"), px(m, "last_price"),
                 m.get("volume_fp", m.get("volume"))] for m in ms]
        self.write({"kind": "snap", "qid": q.qid, "label": label, "markets": rows})
        return ms

    def maybe_trade(self, q: Quake, src: str, mag: float, ts: float) -> None:
        """A source just saw this quake (or raised its magnitude): paper YES on cleared strikes."""
        if mag < TRIGGER_MIN or q.seeded or q.srcs.get(src, {}).get("seed"):
            return
        if q.t is not None and ts - q.t > FRESH_S:
            return
        t = q.t if q.t is not None else ts
        ms = []
        for se in QUOTE_SERIES:                   # only the markets we trade: speed matters here
            try:
                ms += self.markets(se)
            except Exception as exc:  # noqa: BLE001
                log.info("quakefast kalshi %s: %s", se, exc)
        self.snapshot(q, f"seen:{src}:{mag}", ms)
        if not q.triggered:
            q.triggered = True
            for dly in SNAP_DELAYS:
                self.snaps.append((ts + dly, q.qid, dly))
        first = min(q.srcs.values(), key=lambda s: s["first_ts"]) if q.srcs else None
        rank = sorted(q.srcs, key=lambda k: q.srcs[k]["first_ts"]).index(src) + 1 if src in q.srcs else 0
        for m in ms:
            if not magnitude_market(m) or not covers(m, t):
                continue
            x, tk = strike(m), m.get("ticker")
            for mg in MARGINS:
                key = f"{q.qid}|{src}|{tk}|{mg}"
                if mag < x + mg - 1e-9 or any(f["key"] == key for f in self.fills):
                    continue
                ask, size = px(m, "yes_ask"), None
                try:
                    a2, size = ask_depth(self.book(tk))
                    ask = a2 if a2 is not None else ask
                except Exception as exc:  # noqa: BLE001
                    log.info("quakefast book %s: %s", tk, exc)
                row = {"key": key, "qid": q.qid, "src": src, "rank": rank, "margin": mg, "ticker": tk,
                       "strike": x, "mag": mag, "ts": round(ts, 1), "et": et(ts),
                       "lag_s": round(ts - q.t, 1) if q.t else None,
                       "since_first_s": round(ts - first["first_ts"], 1) if first else 0,
                       "bid": px(m, "yes_bid"), "ask": ask, "ask_size": size, "place": q.place}
                if ask is None or ask <= 0 or ask > MAX_ASK:
                    row.update(status="skip", why="no ask" if not ask else f"ask {ask:.2f} > {MAX_ASK}")
                else:
                    row.update(status="open", fee=fee1(ask))
                self.fills.append(row)
                self.write({"kind": "paper", **row})
                if row["status"] == "open":
                    self.real_trade(row, m)

    # -- real money
    def fetch_json(self, url: str):
        r = self.s.get(url, params={"t": int(self.clock())}, timeout=10)
        r.raise_for_status()
        return r.json()

    def read_control(self, now: float) -> None:
        try:
            c = self.fetch_json(CONTROL_URL) or {}
            sw = self.fetch_json(SWITCH_URL) or {}
        except Exception as exc:  # noqa: BLE001
            log.warning("quakefast control read: %s", exc)
            return
        self.rcfg = {**REAL_DEFAULTS, **{k: v for k, v in c.items() if k in REAL_DEFAULTS}}
        self.rcfg["mode"] = str(self.rcfg.get("mode") or "off").lower()
        halted = sw.get("halted") or {}
        self.switch = {"mode": (sw.get("mode") or "").lower() or None,
                       "halted": bool(isinstance(halted, dict) and halted.get("live"))}
        self.ok_at = now

    def real_mode(self) -> str:
        if self.client is None or self.rcfg.get("mode") != "live":
            return "off"
        if self.switch["mode"] in ("off", "paper") or self.switch["halted"]:
            return "off"
        if self.clock() - self.ok_at > CONTROL_STALE_S:
            return "off"
        return "live"

    def cap_used(self) -> float:
        """-(settled real P&L) + cost of open real bets (fees included)."""
        used = 0.0
        for r in self.real:
            if r.get("status") == "settled":
                used -= r["pnl"]
            elif r.get("status") == "open":
                used += r["cost"]
        return round(used, 4)

    def real_trade(self, row: dict, m: dict) -> None:
        """Turn one paper bet into a real 1-ct order when every rail allows it."""
        c = self.rcfg
        if self.real_mode() != "live":
            return
        if row["src"] not in c["sources"] or abs(row["margin"] - float(c["margin"])) > 1e-6:
            return
        ask, size = row.get("ask"), row.get("ask_size")
        if ask is None or ask > float(c["max_ask"]) + 1e-9 or (size or 0) < float(c["min_depth"]):
            return
        if int(_f(m.get("exchange_index")) or 0) != 0:
            return                                    # other balance shard (e.g. Crypto): never
        if any(r["ticker"] == row["ticker"] for r in self.real):
            return                                    # one real order per market
        n = max(1, min(int(c["contracts"]), REAL_MAX_CONTRACTS))
        price = round(ask, 2)
        cost = round(n * (price + fee1(price)), 4)
        cap = float(c["max_loss_usd"])
        if self.cap_used() + cost > cap + 1e-9:
            self.write({"kind": "real_skip", "ticker": row["ticker"], "why": "loss cap",
                        "used": self.cap_used(), "cap": cap})
            self.cap_check()
            return
        rec = {"key": row["key"], "qid": row["qid"], "src": row["src"], "ticker": row["ticker"],
               "strike": row["strike"], "mag": row["mag"], "place": row.get("place"), "price": price,
               "ts": round(self.clock(), 1), "et": et(self.clock()), "lag_s": row.get("lag_s"),
               "placed": n}
        try:
            o = self.client.buy_now(row["ticker"], "yes", price, n,
                                    f"qf-{row['ticker']}-{int(self.clock())}")
            got = fill_qty(o, n)
            rec.update(order_id=o.get("order_id"), filled=got)
        except (OrderRejected, Exception) as exc:  # noqa: BLE001
            rec.update(status="error", filled=0, err=str(exc)[:200])
            self.real.append(rec)
            self.write({"kind": "real", **rec})
            if self.alert and not any(r.get("status") == "error" for r in self.real[:-1]):
                self.alert("Quake bot: Kalshi refused an order",
                           f"{row['ticker']} YES at {price:.2f}: {str(exc)[:150]}. "
                           "Paper keeps running; I'll look at it.")
            return
        if got <= 0:
            rec.update(status="missed", cost=0.0)     # no chasing
        else:
            rec.update(status="open", cost=round(got * (price + fee1(price)), 4))
        self.real.append(rec)
        self.write({"kind": "real", **rec})
        self.cap_check()

    def cap_check(self) -> None:
        """One push when the cap leaves no room for even a 1-contract order at 1c."""
        cap = float(self.rcfg["max_loss_usd"])
        settled_loss = -sum(r["pnl"] for r in self.real if r.get("status") == "settled")
        if self.cap_alerted or settled_loss + 0.02 <= cap:
            return
        self.cap_alerted = True
        self.write({"kind": "cap_used", "settled_loss": round(settled_loss, 2), "cap": cap})
        if self.alert:
            self.alert(f"Quake bot: ${cap:g} loss cap used up",
                       f"The fast quake YES test lost ${settled_loss:.2f} on real money and stopped "
                       "placing real orders. Paper keeps logging.")

    def settle_real(self, now: float) -> None:
        for r in self.real:
            if r.get("status") != "open" or now - r["ts"] < 600 or now - r.get("checked", 0) < 300:
                continue
            r["checked"] = now
            try:
                res = self.s.get(f"{KALSHI}/markets/{r['ticker']}", timeout=10)
                res.raise_for_status()
                m = res.json().get("market") or {}
            except Exception as exc:  # noqa: BLE001
                log.info("quakefast settle %s: %s", r["ticker"], exc)
                continue
            out = (m.get("result") or "").lower()
            if out in ("yes", "no"):
                r["status"], r["result"] = "settled", out
                r["pnl"] = round((r["filled"] if out == "yes" else 0.0) - r["cost"], 4)
                self.write({"kind": "real_settle", "ticker": r["ticker"], "result": out, "pnl": r["pnl"]})
                self.cap_check()

    def run_snaps(self, now: float) -> None:
        due = [x for x in self.snaps if x[0] <= now]
        self.snaps = [x for x in self.snaps if x[0] > now]
        if not due:
            return
        ms = self.all_markets()
        for _, qid, dly in due:
            q = next((x for x in self.quakes if x.qid == qid), None)
            if q:
                self.snapshot(q, f"+{dly}s", ms)

    def tapes(self, now: float) -> None:
        """30 min after a triggered quake: Kalshi's trade tape for the matching daily/hourly markets."""
        for q in self.quakes:
            if not q.triggered or q.tape_done or now - (q.t or q.first_ts) < TAPE_DELAY_S:
                continue
            q.tape_done = True
            t0 = q.t or q.first_ts
            tks = sorted({f["ticker"] for f in self.fills if f["qid"] == q.qid} |
                         {tk for tk in self.quotes if tk.startswith("KXBIGGESTQUAKE-") and
                          covers({"ticker": tk}, t0)})
            out = {}
            for tk in tks[:20]:
                try:
                    r = self.s.get(f"{KALSHI}/markets/trades", params={
                        "ticker": tk, "min_ts": int(t0 - 600), "max_ts": int(t0 + TAPE_DELAY_S),
                        "limit": 1000}, timeout=15)
                    r.raise_for_status()
                    tr = sorted(r.json().get("trades") or [], key=lambda x: x.get("created_time") or "")
                except Exception as exc:  # noqa: BLE001
                    out[tk] = {"err": str(exc)[:80]}
                    continue
                rows = [(_iso(x.get("created_time")), _f(x.get("yes_price_dollars")), _f(x.get("count_fp")),
                         x.get("taker_side")) for x in tr]
                after = [r_ for r_ in rows if r_[0] and r_[0] > t0]
                p50 = next((r_ for r_ in after if (r_[1] or 0) >= 0.5), None)
                out[tk] = {"pre_last": next((r_[1] for r_ in reversed(rows) if r_[0] and r_[0] <= t0), None),
                           "first_after": [[round(a - t0, 1), p, c, s] for a, p, c, s in after[:8]],
                           "first_50c_s": round(p50[0] - t0, 1) if p50 else None, "n_after": len(after)}
            self.write({"kind": "tape", "qid": q.qid, "origin": t0, "markets": out})
            q.kmove = q.kmove or {}
            q.kmove["tape"] = {tk: {k: v.get(k) for k in ("first_50c_s", "n_after")} |
                               {"first_s": (v.get("first_after") or [[None]])[0][0]}
                               for tk, v in out.items() if "err" not in v}

    def settle(self, now: float) -> None:
        for f in self.fills:
            if f.get("status") != "open" or now - f["ts"] < 600:
                continue
            if now - f.get("checked", 0) < 300:
                continue
            f["checked"] = now
            try:
                r = self.s.get(f"{KALSHI}/markets/{f['ticker']}", timeout=10)
                r.raise_for_status()
                m = r.json().get("market") or {}
            except Exception as exc:  # noqa: BLE001
                log.info("quakefast settle %s: %s", f["ticker"], exc)
                continue
            res = (m.get("result") or "").lower()
            if res in ("yes", "no"):
                f["status"], f["result"] = "settled", res
                f["pnl"] = round((1.0 if res == "yes" else 0.0) - f["ask"] - f["fee"], 4)
                self.write({"kind": "settle", "key": f["key"], "result": res, "pnl": f["pnl"]})

    # -- status for the dashboard
    def status(self, now: float) -> dict:
        def agg(rows):
            st = [r for r in rows if r.get("status") == "settled"]
            pnl = sum(r["pnl"] for r in st)
            return {"n": len([r for r in rows if r.get("status") in ("open", "settled")]),
                    "settled": len(st), "won": len([r for r in st if r["pnl"] > 0]),
                    "pnl": round(pnl, 2), "c_per_ct": round(100 * pnl / len(st), 1) if st else None,
                    "skipped": len([r for r in rows if r.get("status") == "skip"])}
        by = {}
        for f in self.fills:
            by.setdefault(f"{f['src']}|{f['margin']}", []).append(f)
        first = [f for f in self.fills if f.get("rank") == 1]
        quakes = []
        for q in sorted(self.quakes, key=lambda x: -(x.t or x.first_ts))[:40]:
            quakes.append({"qid": q.qid, "t": q.t, "et": et(q.t) if q.t else None, "place": q.place,
                           "best_mag": q.best_mag(), "triggered": q.triggered, "kmove": q.kmove,
                           "srcs": {k: {"lag_s": round(v["first_ts"] - q.t, 1) if q.t and not v.get("seed") else None,
                                        "first_mag": v["first_mag"], "mag": v["mag"]}
                                    for k, v in sorted(q.srcs.items(), key=lambda kv: kv[1]["first_ts"])}})
        lags = {}
        for q in self.quakes:
            if not q.t or q.best_mag() < 6.0 or q.seeded:
                continue
            for k, v in q.srcs.items():
                if v.get("seed"):
                    continue
                lags.setdefault(k, []).append(v["first_ts"] - q.t)
        med = {k: {"n": len(v), "median_s": round(sorted(v)[len(v) // 2], 1)} for k, v in lags.items()}
        st = [r for r in self.real if r.get("status") == "settled"]
        rp = sum(r["pnl"] for r in st)
        rct = sum(r["filled"] for r in st)
        real = {"mode": self.real_mode(), "control": {k: v for k, v in self.rcfg.items()},
                "cap": float(self.rcfg["max_loss_usd"]), "cap_used": self.cap_used(),
                "stopped": self.cap_alerted, "orders": len(self.real),
                "filled": len([r for r in self.real if (r.get("filled") or 0) > 0]),
                "missed": len([r for r in self.real if r.get("status") == "missed"]),
                "settled": len(st), "won": len([r for r in st if r["pnl"] > 0]), "pnl": round(rp, 2),
                "c_per_ct": round(100 * rp / rct, 1) if rct else None,
                "open_cost": round(sum(r["cost"] for r in self.real if r.get("status") == "open"), 2),
                "recent": self.real[-20:]}
        return {"at": et(now), "paper_only": real["mode"] != "live", "real": real,
                "totals": agg([f for f in self.fills]),
                "first_source": agg(first),
                "by_source": {k: agg(v) for k, v in sorted(by.items())},
                "fills": self.fills[-60:], "quakes": quakes, "latency_m6": med,
                "feeds": {k: {"ok": v.get("ok"), "err": v.get("err"),
                              "last_ok": et(v["last_ok"]) if v.get("last_ok") else None,
                              "last_err": v.get("last_err_msg")} for k, v in sorted(self.health.items())}}

    def upload(self, now: float) -> None:
        if self.uploader:
            cur = self._hour_file(now).name
            for p in sorted(self.out.glob("quakefast-*.jsonl")):
                if p.name == cur or p.name in self.uploaded:
                    continue
                try:
                    self.uploader(f"quakefast/{p.name}.gz", gzip.compress(p.read_bytes()))
                    self.uploaded.add(p.name)
                except Exception as exc:  # noqa: BLE001
                    log.warning("quakefast upload %s: %s", p.name, exc)
                    break
        if self.status_up:
            try:
                self.status_up({"quakefast.json": json.dumps(self.status(now), indent=1)})
            except Exception as exc:  # noqa: BLE001
                log.warning("quakefast status upload: %s", exc)

    def prune(self, now: float) -> None:
        self.quakes = [q for q in self.quakes if now - (q.t or q.first_ts) < KEEP_QUAKES_S
                       or any(f["qid"] == q.qid and f.get("status") == "open" for f in self.fills)]

    # -- main loop
    def drain(self, timeout: float) -> None:
        end = self.clock() + timeout
        while True:
            left = end - self.clock()
            try:
                item = self.q.get(timeout=max(0.05, left)) if left > 0 else self.q.get_nowait()
            except queue.Empty:
                return
            _, name, ts, dets, status, lat, err = item
            self.on_feed(name, ts, dets, status, lat, err)

    def run(self) -> None:
        stop = threading.Event()
        for f in self.feeds:
            threading.Thread(target=self.feed_loop, args=(f, stop), daemon=True, name=f.name).start()
        if self.ws:
            threading.Thread(target=self.ws_loop, args=(stop,), daemon=True, name="emsc_ws").start()
        self.write({"kind": "start", "feeds": [f.name for f in self.feeds] + (["emsc_ws"] if self.ws else [])})
        nq = nup = nsave = nctl = 0.0
        if self.client is not None:
            try:
                self.write({"kind": "balance", "shards": self.client.shard_balances()})
            except Exception as exc:  # noqa: BLE001
                self.write({"kind": "balance", "err": str(exc)[:120]})
        while True:
            try:
                self.drain(1.0)
                now = self.clock()
                if now >= nctl:
                    nctl = now + 300
                    self.read_control(now)
                if now >= nq:
                    nq = now + 15
                    self.poll_quotes(now)
                self.run_snaps(now)
                self.tapes(now)
                self.settle(now)
                self.settle_real(now)
                if now >= nsave:
                    nsave = now + 60
                    self.prune(now)
                    self.save()
                if now >= nup:
                    nup = now + 300
                    self.upload(now)
            except Exception as exc:  # noqa: BLE001  never die; log and keep going
                log.warning("quakefast step: %s", exc)
                time.sleep(5)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    out = Path(os.environ.get("QUAKEFAST_DIR", "/var/lib/kalshi-quakefast"))
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    client = push = None
    if os.environ.get("QUAKEFAST_REAL", "1") != "0":
        from .m15 import client_from_env
        from .util import Http as _Http
        try:
            client = client_from_env(_Http("kalshi-quakefast/1", min_interval=0.05, retries=1))
        except Exception as exc:  # noqa: BLE001
            log.warning("quakefast: no Kalshi client (%s): paper only", exc)
    for k in list(os.environ):          # the client holds the key in memory; drop it from env
        if k.startswith(("KALSHI", "ANTHROPIC", "OANDA")):
            os.environ.pop(k, None)
    if client is not None:
        from . import notify
        from .util import Http as _Http2, load_config
        cfg, nh = load_config(), _Http2("kalshi-quakefast/1")

        def alert(title: str, body: str) -> None:
            try:
                notify.send(nh, cfg, title, body, priority=3, tags=["earth_americas"])
            except Exception as exc:  # noqa: BLE001
                log.warning("quakefast alert: %s", exc)
        push = alert
    up = status_up = None
    if token:
        from .srclog import github_put
        from .m15 import github_uploader
        from .util import Http
        up = github_put(token, branch="quakefast-data")
        status_up = github_uploader(Http("kalshi-quakefast/1"), token)
    QuakeFast(out, uploader=up, status_up=status_up, client=client, alert=push).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

