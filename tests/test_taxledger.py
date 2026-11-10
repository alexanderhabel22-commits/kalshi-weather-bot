"""Tax ledger: read-only Kalshi fills + settlements -> per-market realized P&L."""
import csv
from datetime import datetime, timezone

from wxbot import taxledger as tl


def fill(ticker, side, action, n, yes_c, ts, oid="o1", cid="", fee="0.01", taker=False, tid=None):
    return {"ticker": ticker, "side": side, "action": action, "count": n, "yes_price": yes_c,
            "no_price": 100 - yes_c, "created_time": ts, "order_id": oid, "client_order_id": cid,
            "fee_cost": fee, "is_taker": taker, "trade_id": tid or f"t-{ticker}-{ts}-{side}"}


class FakeClient:
    def __init__(self, fills, sets, markets=()):
        self.pages = {"/portfolio/fills": ("fills", fills), "/portfolio/settlements": ("settlements", sets)}
        self.markets = list(markets)
        self.calls = []

    def _get(self, path, params=None):
        self.calls.append(path)
        if path == "/markets":
            return {"markets": self.markets}
        if path not in self.pages:
            raise RuntimeError("404")
        key, rows = self.pages[path]
        start = int((params or {}).get("cursor") or 0)
        nxt = start + 2                                  # 2 per page, to exercise the cursor
        return {key: rows[start:nxt], "cursor": str(nxt) if nxt < len(rows) else ""}


def _rows(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def test_ledger_end_to_end(tmp_path, monkeypatch):
    orders = tmp_path / "trade_orders.csv"
    orders.write_text("id,client_id,order_id,mode\nx,wx-fav-1,MAIN1,live\ny,wx-fav-2,PAPER,paper\n")
    monkeypatch.setattr(tl, "TRADE_ORDERS", orders)
    monkeypatch.setattr(tl, "MY_TRADES", tmp_path / "none.csv")
    fills = [
        # main bot buys 2 YES at 90c on a favorite that wins
        fill("KXFAV-1", "yes", "buy", 2, 90, "2026-11-06T15:00:00Z", oid="MAIN1", fee="0.02"),
        # main bot both sides on a weather bracket: NO 55c then YES 27c -> netted to $1 (+18c)
        fill("KXHIGHPHIL-1", "no", "buy", 1, 45, "2026-11-05T23:00:00Z", cid="wx-weather-a"),
        fill("KXHIGHPHIL-1", "yes", "buy", 1, 27, "2026-11-06T01:00:00Z", cid="wx-weather-b"),
        # 15-min bot buys 1 YES at 80c that loses
        fill("KXBTC15M-26NOV101415-15", "yes", "buy", 1, 80, "2026-11-10T19:10:00Z",
             cid="m15-KXBTC15M-1", fee="0.00"),
        # manual trade still open
        fill("KXMANUAL-1", "no", "buy", 3, 40, "2026-11-09T12:00:00Z", oid="APP", fee=None, taker=True),
    ]
    sets = [
        {"ticker": "KXFAV-1", "market_result": "yes", "settled_time": "2026-11-07T02:00:00Z",
         "yes_count": 2, "no_count": 0, "revenue": 200, "fee_cost": "0.00"},
        {"ticker": "KXHIGHPHIL-1", "market_result": "no", "settled_time": "2026-11-07T05:00:00Z",
         "yes_count": 0, "no_count": 0, "revenue": 0},
        {"ticker": "KXBTC15M-26NOV101415-15", "market_result": "no",
         "settled_time": "2026-11-10T19:15:00Z", "yes_count": 1, "no_count": 0, "revenue": 0},
    ]
    client = FakeClient(fills, sets, markets=[{"ticker": "KXFAV-1", "title": "Will X win?"}])
    out = tmp_path / "tax"
    path = tl.run(client, datetime(2026, 11, 10, 22, 0, tzinfo=timezone.utc), out)
    assert all(c.startswith("/portfolio") or c in ("/markets", "/historical/fills", "/historical/settlements")
               for c in client.calls)                    # GETs only, nothing that trades
    m = {(r["ticker"], r["bot"]): r for r in _rows(out / "markets-2026.csv")}
    fav = m[("KXFAV-1", "main bot")]
    assert (fav["cost"], fav["proceeds"], fav["fees"], fav["net"]) == ("1.80", "2.00", "0.02", "0.18")
    assert fav["kalshi_revenue_check"] == "ok" and fav["title"] == "Will X win?"
    phil = m[("KXHIGHPHIL-1", "main bot")]
    assert phil["how_closed"] == "sold before settlement"
    assert (phil["cost"], phil["proceeds"], phil["net"]) == ("0.55", "0.73", "0.16")   # 18c - 2c fees
    btc = m[("KXBTC15M-26NOV101415-15", "15-min bot")]
    assert (btc["net"], btc["closed_et"]) == ("-0.80", "2026-11-10")
    opn = _rows(out / "open-positions.csv")
    assert [(r["bot"], r["no_held"], r["cost_so_far"]) for r in opn] == [("manual", "3", "1.80")]
    f = {r["ticker"] + r["kalshi_side"]: r for r in _rows(out / "fills-2026.csv")}
    assert f["KXMANUAL-1no"]["fee_source"] == "estimated" and f["KXMANUAL-1no"]["fee"] == "0.0600"
    assert f["KXHIGHPHIL-1yes"]["action"] == "sell" and f["KXHIGHPHIL-1yes"]["side"] == "no"
    md = path.read_text()
    assert "| **Total** | 3 |" in md and "**-$0.46**" in md      # 0.18 + 0.16 - 0.80
    assert "Settlement payouts match" in md and "The log is empty." in md


def test_mismatch_and_dollar_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(tl, "TRADE_ORDERS", tmp_path / "none.csv")
    fills = [{"market_ticker": "KXA-1", "side": "yes", "action": "buy", "count_fp": "2.00",
              "yes_price_dollars": "0.6000", "no_price_dollars": "0.4000",
              "created_time": "2026-12-31T23:30:00-05:00", "order_id": "z", "fee_cost": "0.0100",
              "fill_id": "f1"}]
    sets = [{"ticker": "KXA-1", "market_result": "yes", "settled_time": "2027-01-01T06:00:00Z",
             "yes_count": 2, "revenue": 150}]
    fs, ss, _ = tl.fetch_account(FakeClient(fills, sets)._get)
    for x in fs:
        x["bot"] = tl.tag(x, set(), set())
    closed, opened, checks = tl.build_books(fs, ss)
    assert fs[0]["bot"] == "manual" and fs[0]["price"] == 0.6 and fs[0]["count"] == 2
    assert checks == ["KXA-1"] and closed[0]["net"] == round(2 - 1.2 - 0.01, 4)
    tl.write_all(fs, ss, closed, opened, {}, [], checks, datetime(2027, 1, 2, tzinfo=timezone.utc), tmp_path)
    assert (tmp_path / "fills-2026.csv").exists() and (tmp_path / "markets-2027.csv").exists()
    md = (tmp_path / "LEDGER.md").read_text()
    assert "differ from Kalshi" in md and "no 15-min bot fills" in md


def test_fill_side_is_what_was_acquired():
    """Live Kalshi data (Nov 10): a 15-min NO buy at 82c comes back as action "sell",
    side "no", no_price 82c; the side + its price is what was bought, whatever the action."""
    fs = [tl.norm_fill(fill("KXBTC15M-1", "no", "sell", 1, 18, "2026-11-08T04:38:00Z", fee="0")),
          # Hochul: sold 36.94 YES at 99.3c, reported as acquiring NO at 0.7c
          tl.norm_fill(fill("GOV-1", "yes", "buy", 36.94, 96, "2026-11-03T14:39:00Z", fee="0.0993")),
          tl.norm_fill(fill("GOV-1", "no", "sell", 36.94, 99.3, "2026-11-04T02:01:00Z", fee="0.0161"))]
    for f in fs:
        f["bot"] = "manual"
    ts = datetime(2026, 11, 9, tzinfo=timezone.utc)
    sets = [{"ticker": "KXBTC15M-1", "result": "no", "value": None, "ts": ts, "yes": 0, "no": 1,
             "revenue": 1.0, "fee": 0.0},
            {"ticker": "GOV-1", "result": "yes", "value": None, "ts": ts, "yes": 36.94, "no": 36.94,
             "revenue": 0.0, "fee": 0.1154}]          # fee_cost = the fills' fees again
    closed, _, checks = tl.build_books(fs, sets)
    r = {x["ticker"]: x for x in closed}
    assert (r["KXBTC15M-1"]["cost"], r["KXBTC15M-1"]["net"], r["KXBTC15M-1"]["check"]) == (0.82, 0.18, "ok")
    gov = r["GOV-1"]
    assert gov["how"] == "sold before settlement" and gov["fees"] == 0.1154
    assert gov["net"] == round(36.94 * 0.993 - 36.94 * 0.96 - 0.1154, 4) and not checks
