"""Second pass (read-only): trades in the last 7 days before close for econ-release
markets that closed since 2025-01-01, plus GDPNow history. Writes into argv[1]."""
from __future__ import annotations

import csv
import gzip
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from pull import S, get, log, ts, f, LOG  # noqa: E402

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
OUT.mkdir(parents=True, exist_ok=True)
SERIES = {"KXCPI", "KXCPIYOY", "KXCPICORE", "KXCPICOREYOY", "KXPAYROLLS", "KXU3", "KXGDP",
          "KXJOBLESSCLAIMS", "KXPCECORE", "KXFED", "KXFEDDECISION"}
SINCE = "2025-01-01"
GDPNOW = [
    "https://www.atlantafed.org/-/media/Project/Atlanta/FRBA/Documents/cqer/researchcq/gdpnow/GDPTrackingModelDataAndForecasts.xlsx",
    "https://www.atlantafed.org/-/media/documents/cqer/researchcq/gdpnow/GDPTrackingModelDataAndForecasts.xlsx",
    "https://www.atlantafed.org/cqer/research/gdpnow",
]


def main():
    for i, u in enumerate(GDPNOW):
        try:
            r = S.get(u, timeout=90, headers={"User-Agent": "Mozilla/5.0 (research)"})
            log("gdpnow", i, r.status_code, len(r.content), r.headers.get("content-type"))
            if r.ok:
                (OUT / f"gdpnow_{i}.{'html' if 'html' in (r.headers.get('content-type') or '') else 'xlsx'}").write_bytes(r.content)
        except requests.RequestException as e:
            log("gdpnow", i, "ERR", str(e)[:100])
    mk = list(csv.DictReader(open(Path(__file__).parent / "markets_index.csv")))
    mk = [m for m in mk if m["series"] in SERIES and m["close"] >= SINCE and float(m["volume"]) >= 500]
    log("markets", len(mk))

    def trades(m):
        out, cur = [], None
        lo = ts(m["close"]) - 7 * 86400
        paths = ["/historical/trades", "/markets/trades"] if m["hist"] == "1" else ["/markets/trades"]
        for path in paths:
            for _ in range(25):
                p = {"ticker": m["ticker"], "limit": 1000, "min_ts": lo}
                if cur:
                    p["cursor"] = cur
                js = get(path, p)
                if not js:
                    break
                for t in js.get("trades") or []:
                    out.append([m["ticker"], t.get("created_time"),
                                t.get("yes_price_dollars", t.get("yes_price")),
                                f(t.get("count_fp", t.get("count"))), t.get("taker_side")])
                cur = js.get("cursor")
                if not cur:
                    break
            if out:
                break
        return out

    with ThreadPoolExecutor(6) as ex:
        res = list(ex.map(trades, mk))
    with gzip.open(OUT / "trades.csv.gz", "wt", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "time", "yes_price", "count", "taker_side"])
        for x in res:
            w.writerows(x)
    log("trades", sum(len(x) for x in res), "markets with trades", sum(1 for x in res if x))


if __name__ == "__main__":
    try:
        main()
    finally:
        (OUT / "log.txt").write_text("\n".join(LOG))
