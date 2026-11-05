"""Out-of-sample check for the Mentions maker lead (read-only public data).

Pulls settled Mentions-category markets that closed between ~7 months ago
and Oct 9, 2026 (before the first probe's window), plus all their trades,
from both the live and /historical endpoints. Writes into argv[1].
"""
from __future__ import annotations

import csv
import gzip
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from probe import f, get, log, paginate, LOG  # noqa: E402

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
OUT.mkdir(parents=True, exist_ok=True)
START = int(datetime(2026, 4, 1, tzinfo=timezone.utc).timestamp())
END = int(datetime(2026, 10, 9, tzinfo=timezone.utc).timestamp())


def main():
    js = get("/series", {"category": "Mentions"}) or {}
    series = [s["ticker"] for s in js.get("series") or [] if s.get("category") == "Mentions"]
    log("mentions series", len(series))
    rows, seen = [], set()
    for s in series:
        for path, extra in (("/markets", {"status": "settled"}), ("/historical/markets", {})):
            ms = paginate(path, "markets", {"series_ticker": s, "limit": 1000, "min_close_ts": START,
                                            **extra}, 50)
            for m in ms:
                tk = m.get("ticker")
                if tk in seen or m.get("result") not in ("yes", "no"):
                    continue
                ct = m.get("close_time") or ""
                ts = int(datetime.fromisoformat(ct.replace("Z", "+00:00")).timestamp()) if ct else 0
                if ts >= END:
                    continue
                vol = f(m.get("volume_fp", m.get("volume"))) or 0
                if vol < 10:
                    continue
                seen.add(tk)
                rows.append({"ticker": tk, "series": s, "category": "Mentions",
                             "open": m.get("open_time"), "close": ct, "result": m.get("result"),
                             "volume": vol, "settle_value": "", "title": (m.get("title") or "")[:120],
                             "hist": int(path.startswith("/historical"))})
    log("markets", len(rows), "historical", sum(r["hist"] for r in rows))
    with gzip.open(OUT / "settled.csv.gz", "wt", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else ["ticker"])
        w.writeheader()
        w.writerows(rows)

    def trades(r):
        out, cur = [], None
        path = "/historical/trades" if r["hist"] else "/markets/trades"
        for _ in range(30):
            p = {"ticker": r["ticker"], "limit": 1000}
            if cur:
                p["cursor"] = cur
            js = get(path, p)
            if not js:
                break
            for t in js.get("trades") or []:
                out.append([r["ticker"], t.get("created_time"),
                            t.get("yes_price_dollars", t.get("yes_price")),
                            t.get("count_fp", t.get("count")), t.get("taker_side")])
            cur = js.get("cursor")
            if not cur:
                break
        return out

    with ThreadPoolExecutor(6) as ex:
        res = list(ex.map(trades, rows))
    with gzip.open(OUT / "trades.csv.gz", "wt", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "time", "yes_price", "count", "taker_side"])
        for x in res:
            w.writerows(x)
    with open(OUT / "sample.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "truncated", "in_top"])
        for r, x in zip(rows, res):
            w.writerow([r["ticker"], int(len(x) >= 30000), 0])
    log("trades", sum(map(len, res)), "markets with trades", sum(1 for x in res if x))


if __name__ == "__main__":
    try:
        main()
    finally:
        (OUT / "log.txt").write_text("\n".join(LOG))
