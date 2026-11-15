from datetime import datetime, timezone

from wxbot import quotes


class Http:
    def get_json(self, url, params=None, headers=None):
        tk = url.rsplit("/", 1)[1]
        return {"market": {"ticker": tk, "yes_bid_dollars": "0.22", "yes_ask_dollars": "0.24",
                           "last_price_dollars": "0.23", "volume": 900, "status": "active"}}


def test_snapshot_writes_no_side_prices(tmp_path, monkeypatch):
    monkeypatch.setattr(quotes, "QUOTES", tmp_path / "quotes.json")
    out = quotes.snapshot(Http(), datetime(2026, 11, 5, tzinfo=timezone.utc), ["KX-A"])
    q = out["quotes"]["KX-A"]
    assert (q["no_bid"], q["no_ask"], q["yes_bid"]) == (0.76, 0.78, 0.22)
    assert (tmp_path / "quotes.json").exists()


def test_open_tickers_reads_both_ledgers(tmp_path, monkeypatch):
    (tmp_path / "trade_orders.csv").write_text("ticker,status\nA,resting\nB,settled\nC,filled\n")
    (tmp_path / "paper_orders.csv").write_text("ticker,status\nD,open\nE,filled\nA,open\n")
    monkeypatch.setattr(quotes, "DATA_DIR", tmp_path)
    assert quotes.open_tickers() == ["A", "C", "D"]


def test_open_tickers_puts_live_first_and_keeps_them_under_cap(tmp_path, monkeypatch):
    rows = "ticker,status,mode\n" + "".join(f"P{i},filled,paper\n" for i in range(5)) + "L1,filled,live\nL2,resting,live\n"
    (tmp_path / "trade_orders.csv").write_text(rows)
    monkeypatch.setattr(quotes, "DATA_DIR", tmp_path)
    monkeypatch.setattr(quotes, "MAX_TICKERS", 3)
    assert quotes.open_tickers() == ["L1", "L2", "P0"]


def test_snapshot_uses_batch_lookup(tmp_path, monkeypatch):
    class Batch:
        calls = 0

        def get_json(self, url, params=None, headers=None):
            Batch.calls += 1
            tks = params["tickers"].split(",")
            return {"markets": [{"ticker": tk, "yes_bid_dollars": "0.40", "yes_ask_dollars": "0.45",
                                 "status": "active"} for tk in tks]}

    monkeypatch.setattr(quotes, "QUOTES", tmp_path / "quotes.json")
    out = quotes.snapshot(Batch(), datetime(2026, 11, 15, tzinfo=timezone.utc), [f"KX-{i}" for i in range(150)])
    assert len(out["quotes"]) == 150 and Batch.calls == 2
