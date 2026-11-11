"""Tax-ready ledger of every real trade, fee and settlement on the Kalshi account.

READ-ONLY: it only calls GET endpoints (fills, settlements, market titles) and never
places, changes or cancels an order. Kalshi's own records are the source of truth:

  GET /portfolio/fills         every fill on the account (main bot, 15-min bot, manual)
  GET /portfolio/settlements   every settled market with Kalshi's payout ("revenue")

One API key sees the whole account, so the main bot's key also returns the 15-min
bot's fills (both keys belong to the same Kalshi account). Each fill is tagged:

  main bot   its order id or client id is in data/trade_orders.csv (client ids "wx-...")
  15-min bot a 15-minute series (KX...15M) or a client id starting "m15-"
  manual     anything else (Alex's own trades in the app)

data/my_trades.csv is Alex's hand-written log of manual trades. It is shown on its own
and never added to the totals: the manual fills already come from Kalshi.

Each Kalshi fill names the side it acquired and that side's price. Positions follow the
Kalshi app: acquiring YES while holding NO closes the NO (a sale of NO at 1 - price). Per market
and bot: cost = money paid to open, proceeds = money from closing sales + settlement
payout, fees = trading + settlement fees, net = proceeds - cost - fees
(Kalshi's per-settlement fee_cost is the same trading fees again, so it is not added). A market counts
in the tax year (US Eastern) it was settled, or of its last fill if it was closed by
selling before settlement. Still-open positions are listed but not counted.

Writes reports/tax/: LEDGER.md (summary by year, bot and month), and per year
fills-YYYY.csv, settlements-YYYY.csv, markets-YYYY.csv; open-positions.csv.
"""
from __future__ import annotations

import csv
import logging
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .util import DATA_DIR

log = logging.getLogger("wxbot")

ET = ZoneInfo("America/New_York")
OUT = DATA_DIR.parent / "reports" / "tax"
TRADE_ORDERS = DATA_DIR / "trade_orders.csv"
MY_TRADES = DATA_DIR / "my_trades.csv"
BOTS = ("main bot", "15-min bot", "rewards bot", "manual")

FILL_COLS = ["date_et", "time_et", "time_utc", "bot", "ticker", "title", "kalshi_action",
             "kalshi_side", "action", "side", "contracts", "price", "amount", "fee",
             "fee_source", "taker", "order_id", "client_order_id", "fill_id"]
SETTLE_COLS = ["date_et", "time_et", "time_utc", "ticker", "title", "result", "yes_contracts",
               "no_contracts", "revenue", "fee"]
MARKET_COLS = ["closed_et", "opened_et", "bot", "ticker", "title", "result", "how_closed",
               "contracts_bought", "cost", "sale_proceeds", "settlement_payout", "proceeds",
               "fees", "net", "kalshi_revenue_check"]
OPEN_COLS = ["opened_et", "bot", "ticker", "title", "yes_held", "no_held", "cost_so_far",
             "sale_proceeds", "fees", "net_so_far"]


# --------------------------------------------------------------------------- parsing

def _f(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _dollars(d: dict, name: str):
    """A money/price field in dollars: `<name>_dollars` (string $) wins, else the old
    integer-cents `<name>`. None if neither is there."""
    v = _f(d.get(name + "_dollars"))
    if v is not None:
        return v
    v = _f(d.get(name))
    return None if v is None else v / 100.0


def _count(d: dict, name: str = "count") -> float:
    v = _f(d.get(name + "_fp"))
    if v is None:
        v = _f(d.get(name))
    return v or 0.0


def _when(d: dict, *keys) -> datetime | None:
    for k in keys:
        v = d.get(k)
        if v in (None, ""):
            continue
        if isinstance(v, (int, float)) or str(v).isdigit():
            t = float(v)
            return datetime.fromtimestamp(t / 1000.0 if t > 1e11 else t, timezone.utc)
        try:
            dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        except ValueError:
            continue
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def kalshi_fee(price: float, count: float, taker: bool) -> float:
    """Kalshi's published fee, rounded up to the cent (used only if a fill has no fee)."""
    rate = 0.07 if taker else 0.0175
    return math.ceil(round(rate * count * price * (1 - price) * 100, 6)) / 100.0


def norm_fill(f: dict) -> dict | None:
    ticker = f.get("ticker") or f.get("market_ticker") or ""
    side = str(f.get("side") or "").lower()
    action = str(f.get("action") or "").lower()
    n = _count(f)
    ts = _when(f, "created_time", "ts", "created_ts")
    if not ticker or side not in ("yes", "no") or n <= 0 or ts is None:
        return None
    yes = _dollars(f, "yes_price")
    no = _dollars(f, "no_price")
    if yes is None and no is not None:
        yes = 1.0 - no
    if no is None and yes is not None:
        no = 1.0 - yes
    if yes is None:
        return None
    price = yes if side == "yes" else no
    taker = bool(f.get("is_taker"))
    fee = _f(f.get("fee_cost"))
    if fee is None:
        fee = _dollars(f, "fee")
    src = "kalshi"
    if fee is None:
        fee, src = kalshi_fee(price, n, taker), "estimated"
    fid = f.get("fill_id") or f.get("trade_id") or f"{f.get('order_id')}|{ts.isoformat()}|{n}"
    return {"id": str(fid), "trade_id": str(f.get("trade_id") or ""), "ts": ts, "ticker": ticker,
            "side": side, "action": action, "count": n, "price": round(price, 4), "taker": taker,
            "fee": round(fee, 4), "fee_source": src, "order_id": str(f.get("order_id") or ""),
            "client_order_id": str(f.get("client_order_id") or "")}


def norm_settlement(s: dict) -> dict | None:
    ticker = s.get("ticker") or s.get("market_ticker") or ""
    ts = _when(s, "settled_time", "settled_ts", "ts")
    if not ticker or ts is None:
        return None
    result = str(s.get("market_result") or s.get("result") or "").lower()
    value = _dollars(s, "value")        # scalar markets: YES pays this much
    fee = _f(s.get("fee_cost"))
    if fee is None:
        fee = _dollars(s, "fee") or 0.0
    return {"ticker": ticker, "ts": ts, "result": result, "value": value,
            "yes": _count(s, "yes_count"), "no": _count(s, "no_count"),
            "revenue": _dollars(s, "revenue") or 0.0, "fee": round(fee, 4)}


def yes_value(st: dict) -> float | None:
    """What one YES contract paid at settlement ($1 or 0; the value for scalar markets)."""
    r = st["result"]
    if r == "yes":
        return 1.0
    if r == "no":
        return 0.0
    if st.get("value") is not None and r not in ("void", ""):
        v = st["value"]
        return v if v <= 1 else v / 100.0
    return None


# --------------------------------------------------------------------------- fetching

def fetch_pages(get, path: str, key: str, max_pages: int = 1000) -> list:
    out, cursor = [], None
    for _ in range(max_pages):
        params = {"limit": 200}
        if cursor:
            params["cursor"] = cursor
        js = get(path, params) or {}
        out.extend(js.get(key) or [])
        cursor = js.get("cursor")
        if not cursor:
            return out
    raise RuntimeError(f"{path}: more than {max_pages} pages")


def fetch_account(get) -> tuple[list, list, list]:
    """(fills, settlements, notes). Older records may live under /historical; those
    calls are best-effort and de-duplicated against the live listing."""
    notes = []
    raw_f = fetch_pages(get, "/portfolio/fills", "fills")
    raw_s = fetch_pages(get, "/portfolio/settlements", "settlements")
    for path, key, dst in (("/historical/fills", "fills", raw_f),
                           ("/historical/settlements", "settlements", raw_s)):
        try:
            dst.extend(fetch_pages(get, path, key, max_pages=200))
        except Exception as exc:  # endpoint may not exist; the live listing is enough
            log.info("%s skipped: %s", path, exc)
    fills, seen = [], set()
    for f in raw_f:
        n = norm_fill(f)
        if n is None:
            notes.append(f"skipped a fill Kalshi sent in an unknown shape: {sorted(f)[:8]}")
            continue
        k = n["trade_id"] + "|" + n["order_id"] if n["trade_id"] else n["id"]
        if k in seen:
            continue
        seen.add(k)
        fills.append(n)
    sets, seen = [], set()
    for s in raw_s:
        n = norm_settlement(s)
        if n is None or n["ticker"] in seen:
            continue
        seen.add(n["ticker"])
        sets.append(n)
    fills.sort(key=lambda r: (r["ts"], r["id"]))
    sets.sort(key=lambda r: (r["ts"], r["ticker"]))
    return fills, sets, notes


def fetch_titles(get, tickers) -> dict:
    out, tickers = {}, sorted(set(tickers))
    for i in range(0, len(tickers), 100):
        try:
            js = get("/markets", {"tickers": ",".join(tickers[i:i + 100]), "limit": 100}) or {}
        except Exception as exc:  # titles are a nicety
            log.info("titles: %s", exc)
            continue
        for m in js.get("markets") or []:
            t = (m.get("title") or "").strip()
            sub = (m.get("yes_sub_title") or m.get("subtitle") or "").strip()
            out[m.get("ticker")] = f"{t} ({sub})" if sub and sub not in t else t
    return out


# --------------------------------------------------------------------------- tagging

def bot_ids(path: Path | None = None) -> tuple[set, set, dict]:
    """Main-bot order ids, client ids, and {ticker: side held} for markets the bot sold
    early (its exit orders aren't logged by id, and Kalshi's fills carry no client id)."""
    oids, cids, exits = set(), set(), {}
    path = path or TRADE_ORDERS
    if path.exists():
        with path.open(newline="") as fh:
            for r in csv.DictReader(fh):
                if (r.get("mode") or "") != "live":
                    continue
                if r.get("order_id"):
                    oids.add(r["order_id"])
                if r.get("client_id"):
                    cids.add(r["client_id"])
                if (r.get("result") or "") == "exit" and r.get("ticker"):
                    exits[r["ticker"]] = (r.get("side") or "").lower()
    return oids, cids, exits


def is_m15(ticker: str) -> bool:
    return ticker.split("-", 1)[0].upper().endswith("15M")


def tag(fill: dict, oids: set, cids: set, exits: dict | None = None) -> str:
    cid = fill.get("client_order_id") or ""
    if fill.get("order_id") in oids or cid in cids or cid.startswith("wx-") or \
            (cid.startswith("x") and "-wx-" in cid):          # exits: "x<ddHHMMSS>-wx-..."
        return "main bot"
    held = (exits or {}).get(fill["ticker"])
    if held and fill["side"] != held:              # the main bot's early sale of what it held
        return "main bot"
    if cid.startswith("lip-"):                     # liquidity-reward test (wxbot/lip.py)
        return "rewards bot"
    if cid.startswith("m15-") or is_m15(fill["ticker"]):
        return "15-min bot"
    return "manual"


# --------------------------------------------------------------------------- books

def build_books(fills: list, sets: list) -> tuple[list, list, list]:
    """(closed market rows, open position rows, account-level revenue checks)."""
    settle = {s["ticker"]: s for s in sets}
    books = {}
    for f in fills:
        b = books.setdefault((f["ticker"], f["bot"]), {
            "ticker": f["ticker"], "bot": f["bot"], "yes": 0.0, "no": 0.0, "cost": 0.0,
            "sales": 0.0, "fees": 0.0, "bought": 0.0, "first": f["ts"], "last": f["ts"]})
        b["last"] = f["ts"]
        b["fees"] += f["fee"]
        side, n, p = f["side"], f["count"], f["price"]
        other, q = ("no" if side == "yes" else "yes"), round(1.0 - p, 4)
        # Kalshi's fills name the side the order ACQUIRED and that side's price; "action"
        # follows the single YES book (selling YES = acquiring NO) and is not needed.
        # Checked Nov 10: summing fills this way matches every settlement's YES/NO counts.
        # Acquiring the side opposite to what is held closes it (Kalshi pairs YES with NO
        # for $1), which is a sale of the held side at 1 - price.
        close = min(n, b[other])
        b[other] -= close
        b["sales"] += close * q
        opn = n - close
        b[side] += opn
        b["cost"] += opn * p
        b["bought"] += opn
        f["action_n"], f["side_n"] = ("sell", other) if close and not opn else ("buy", side)
        f["amount"] = round(close * q - opn * p, 4)
    closed, opened, acct = [], [], defaultdict(float)
    for (ticker, bot), b in sorted(books.items(), key=lambda kv: (kv[1]["first"], kv[0])):
        st = settle.get(ticker)
        yv = yes_value(st) if st else None
        row = {"bot": bot, "ticker": ticker, "opened": b["first"],
               "contracts_bought": round(b["bought"], 2), "cost": round(b["cost"], 4),
               "sale_proceeds": round(b["sales"], 4)}
        flat = b["yes"] < 1e-9 and b["no"] < 1e-9
        if flat:
            row.update(closed=b["last"], result=(st or {}).get("result", ""), how="sold before settlement",
                       payout=0.0, fees=b["fees"])
        elif st and (yv is not None or st["result"] == "void"):
            if yv is None:        # void: Kalshi refunds; take its revenue for this book's share
                payout = None
            else:
                payout = b["yes"] * yv + b["no"] * (1 - yv)
            acct[ticker] += payout if payout is not None else 0.0
            row.update(closed=st["ts"], result=st["result"], how="settled",
                       payout=payout, fees=b["fees"])
        else:
            opened.append({**row, "yes_held": round(b["yes"], 2), "no_held": round(b["no"], 2),
                           "fees": round(b["fees"], 4),
                           "net_so_far": round(b["sales"] - b["cost"] - b["fees"], 4)})
            continue
        closed.append(row)
    # Kalshi's settlement fee_cost is the total of that market's trading fees (checked
    # Nov 10 on all 730 settlements), already counted from the fills, so it is not added.
    checks = []
    for r in closed:
        st = settle.get(r["ticker"])
        if r["payout"] is None:                      # void
            r["payout"] = st["revenue"] if st else 0.0
            r["check"] = "void: Kalshi refund used"
        elif st and r["how"] == "settled":
            diff = round(st["revenue"] - acct[r["ticker"]], 2)
            pairs = min(st["yes"], st["no"])
            if abs(diff) < 0.005:
                r["check"] = "ok"
            elif pairs and abs(diff - pairs) < 0.005:
                r["check"] = "ok (Kalshi paired YES with NO at settlement)"
            else:
                r["check"] = f"MISMATCH: Kalshi paid {st['revenue']:.2f}, fills say {acct[r['ticker']]:.2f}"
            if r["check"].startswith("MISMATCH"):
                checks.append(r["ticker"])
        else:
            r["check"] = ""
        r["proceeds"] = round(r["sale_proceeds"] + r["payout"], 4)
        r["fees"] = round(r["fees"], 4)
        r["net"] = round(r["proceeds"] - r["cost"] - r["fees"], 4)
    closed.sort(key=lambda r: (r["closed"], r["ticker"], r["bot"]))
    return closed, opened, checks


# --------------------------------------------------------------------------- output

def _et(ts: datetime) -> datetime:
    return ts.astimezone(ET)


def _money(x: float) -> str:
    return f"-${-x:,.2f}" if x < -0.0049 else f"${x:,.2f}"


def _write(path: Path, cols: list, rows: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


def manual_log(path: Path | None = None) -> list:
    path = path or MY_TRADES
    if not path.exists():
        return []
    with path.open(newline="") as fh:
        return [r for r in csv.DictReader(fh) if any((v or "").strip() for v in r.values())]


def write_all(fills, sets, closed, opened, titles, notes, checks, now: datetime,
              out: Path = OUT) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    years = sorted({_et(f["ts"]).year for f in fills} | {_et(s["ts"]).year for s in sets}
                   | {_et(r["closed"]).year for r in closed})
    for y in years:
        _write(out / f"fills-{y}.csv", FILL_COLS, [{
            "date_et": f"{_et(f['ts']):%Y-%m-%d}", "time_et": f"{_et(f['ts']):%H:%M:%S}",
            "time_utc": f["ts"].isoformat(), "bot": f["bot"], "ticker": f["ticker"],
            "title": titles.get(f["ticker"], ""), "kalshi_action": f["action"], "kalshi_side": f["side"],
            "action": f.get("action_n", f["action"]), "side": f.get("side_n", f["side"]),
            "contracts": f"{f['count']:g}", "price": f"{f['price']:.4f}", "amount": f"{f.get('amount', 0):.4f}",
            "fee": f"{f['fee']:.4f}", "fee_source": f["fee_source"], "taker": "yes" if f["taker"] else "no",
            "order_id": f["order_id"], "client_order_id": f["client_order_id"], "fill_id": f["id"]}
            for f in fills if _et(f["ts"]).year == y])
        _write(out / f"settlements-{y}.csv", SETTLE_COLS, [{
            "date_et": f"{_et(s['ts']):%Y-%m-%d}", "time_et": f"{_et(s['ts']):%H:%M:%S}",
            "time_utc": s["ts"].isoformat(), "ticker": s["ticker"], "title": titles.get(s["ticker"], ""),
            "result": s["result"], "yes_contracts": f"{s['yes']:g}", "no_contracts": f"{s['no']:g}",
            "revenue": f"{s['revenue']:.2f}", "fee": f"{s['fee']:.4f}"}
            for s in sets if _et(s["ts"]).year == y])
        _write(out / f"markets-{y}.csv", MARKET_COLS, [{
            "closed_et": f"{_et(r['closed']):%Y-%m-%d}", "opened_et": f"{_et(r['opened']):%Y-%m-%d}",
            "bot": r["bot"], "ticker": r["ticker"], "title": titles.get(r["ticker"], ""),
            "result": r["result"], "how_closed": r["how"], "contracts_bought": f"{r['contracts_bought']:g}",
            "cost": f"{r['cost']:.2f}", "sale_proceeds": f"{r['sale_proceeds']:.2f}",
            "settlement_payout": f"{r['payout']:.2f}", "proceeds": f"{r['proceeds']:.2f}",
            "fees": f"{r['fees']:.2f}", "net": f"{r['net']:.2f}", "kalshi_revenue_check": r["check"]}
            for r in closed if _et(r["closed"]).year == y])
    _write(out / "open-positions.csv", OPEN_COLS, [{
        "opened_et": f"{_et(r['opened']):%Y-%m-%d}", "bot": r["bot"], "ticker": r["ticker"],
        "title": titles.get(r["ticker"], ""), "yes_held": f"{r['yes_held']:g}", "no_held": f"{r['no_held']:g}",
        "cost_so_far": f"{r['cost']:.2f}", "sale_proceeds": f"{r['sale_proceeds']:.2f}",
        "fees": f"{r['fees']:.2f}", "net_so_far": f"{r['net_so_far']:.2f}"} for r in opened])
    md = summary(fills, closed, opened, notes, checks, years, now)
    (out / "LEDGER.md").write_text(md)
    return out / "LEDGER.md"


def summary(fills, closed, opened, notes, checks, years, now: datetime) -> str:
    L = ["# Kalshi trade ledger (for taxes)", "",
         f"Built {_et(now):%b %d, %Y %I:%M %p} ET straight from Kalshi's own fill and settlement "
         "records (read-only). Every real trade on the account is here: the main bot, the 15-min "
         "bot and your own manual trades. Dates are US Eastern.", "",
         "**Not tax advice.** Kalshi sends its own year-end tax form; use this to check it and to "
         "back up your numbers. A tax preparer decides how event contracts are reported.", ""]
    for y in reversed(years):
        rows = [r for r in closed if _et(r["closed"]).year == y]
        yf = [f for f in fills if _et(f["ts"]).year == y]
        L += [f"## {y}", ""]
        if not rows:
            L += ["No markets closed this year yet.", ""]
        tot = lambda k, rs=rows: sum(r[k] for r in rs)  # noqa: E731
        L += ["| | Markets | Cost | Proceeds | Fees | Net |", "|---|---:|---:|---:|---:|---:|"]
        for bot in BOTS:
            rs = [r for r in rows if r["bot"] == bot]
            if rs:
                L.append(f"| {bot} | {len(rs)} | {_money(tot('cost', rs))} | {_money(tot('proceeds', rs))} "
                         f"| {_money(tot('fees', rs))} | **{_money(tot('net', rs))}** |")
        L.append(f"| **Total** | {len(rows)} | {_money(tot('cost'))} | {_money(tot('proceeds'))} "
                 f"| {_money(tot('fees'))} | **{_money(tot('net'))}** |")
        wins = sum(1 for r in rows if r["net"] > 0.0049)
        losses = sum(1 for r in rows if r["net"] < -0.0049)
        L += ["", f"{len(yf)} fills, {sum(f['count'] for f in yf):g} contracts traded, "
              f"{_money(sum(f['fee'] for f in yf))} in trading fees. Markets closed: {wins} won, "
              f"{losses} lost, {len(rows) - wins - losses} broke even.", ""]
        months = sorted({_et(r["closed"]).month for r in rows})
        if months:
            L += ["| Month | " + " | ".join(b.capitalize() for b in BOTS) + " | Total |",
                  "|---|" + "---:|" * (len(BOTS) + 1)]
            for m in months:
                mr = [r for r in rows if _et(r["closed"]).month == m]
                cells = [_money(sum(r["net"] for r in mr if r["bot"] == b)) for b in BOTS]
                L.append(f"| {datetime(y, m, 1):%B} | " + " | ".join(cells)
                         + f" | **{_money(sum(r['net'] for r in mr))}** |")
            L.append("")
        L += [f"Files: `markets-{y}.csv` (one line per market: cost, proceeds, fees, net), "
              f"`fills-{y}.csv` (every fill), `settlements-{y}.csv` (Kalshi's payouts).", ""]
    if opened:
        L += ["## Still open", "",
              f"{len(opened)} positions not settled yet (not counted above): "
              f"{_money(sum(r['cost'] for r in opened))} at cost. See `open-positions.csv`.", ""]
    est = sum(1 for f in fills if f["fee_source"] == "estimated")
    L += ["## Checks", ""]
    L.append("- Fills by bot: " + ", ".join(f"{b} {sum(1 for f in fills if f['bot'] == b)}" for b in BOTS) + ".")
    if not any(f["bot"] == "15-min bot" for f in fills):
        L.append("- WARNING: no 15-min bot fills came back. Its fills may sit on Kalshi's second "
                 "exchange shard; rerun this with the 15-min bot's key.")
    L.append("- Settlement payouts match Kalshi's paid amounts." if not checks else
             f"- WARNING: {len(checks)} settlements differ from Kalshi's payout: {', '.join(checks[:10])}.")
    if est:
        L.append(f"- {est} fills had no fee from Kalshi; those fees use Kalshi's published formula.")
    for n in notes[:10]:
        L.append(f"- {n}")
    log_rows = manual_log()
    L += ["", "## Your manual trade log (data/my_trades.csv)", "",
          "Shown for reference only and NOT added to the totals (your manual trades are already in "
          "the Kalshi fills above, tagged \"manual\").", ""]
    if log_rows:
        cols = list(log_rows[0].keys())
        L += ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
        L += ["| " + " | ".join((r.get(c) or "") for c in cols) + " |" for r in log_rows]
    else:
        L.append("The log is empty.")
    return "\n".join(L) + "\n"


def run(client, now: datetime, out: Path = OUT) -> Path:
    get = client._get
    fills, sets, notes = fetch_account(get)
    oids, cids, exits = bot_ids()
    for f in fills:
        f["bot"] = tag(f, oids, cids, exits)
    closed, opened, checks = build_books(fills, sets)
    titles = fetch_titles(get, [f["ticker"] for f in fills])
    path = write_all(fills, sets, closed, opened, titles, notes, checks, now, out)
    log.info("tax ledger: %d fills, %d settlements, %d closed markets, %d open",
             len(fills), len(sets), len(closed), len(opened))
    return path
