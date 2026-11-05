import csv
from datetime import datetime, timedelta, timezone

from wxbot import equity

T0 = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(equity, "DATA_DIR", tmp_path)
    monkeypatch.setattr(equity, "EQUITY", tmp_path / "equity.csv")
    (tmp_path / "trade_orders.csv").write_text(
        "mode,strategy,ticker,side,price,count,filled,status,pnl\n"
        "paper,mentions,A,no,0.80,10,10,filled,\n"      # held 10 NO @80c
        "paper,mentions,B,no,0.90,5,0,resting,\n"       # resting only: exposure, not held
        "paper,favorites,C,yes,0.95,4,4,settled,0.20\n"
        "paper,weather,W,yes,0.40,1,1,settled,0.60\n"
        "live,mentions,D,yes,0.50,2,2,filled,\n")
    (tmp_path / "paper_orders.csv").write_text(
        "ticker,side,status,pnl\nW,yes,filled,0.60\nX,no,filled,-0.30\nY,yes,open,\n")


def test_marks_realized_unrealized_exposure(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    # A: yes 0.10/0.12 -> NO bid 0.88, ask 0.90 -> mid 0.89; D: yes mid 0.45
    m = equity.marks({"A": {"yes_bid": 0.10, "yes_ask": 0.12}, "D": {"yes_bid": 0.44, "yes_ask": 0.46}})
    pm = m["paper"]["mentions"]
    assert round(pm["unrealized"], 4) == 0.9  # (0.89 - 0.80) * 10
    assert round(pm["exposure"], 2) == 12.5   # 10*0.80 + 5*0.90
    assert pm["held"] == 1
    assert m["paper"]["favorites"]["realized"] == 0.2
    # W counted once (trader ledger), X from the research log
    assert round(m["paper"]["weather"]["realized"], 2) == 0.30
    assert round(m["live"]["mentions"]["unrealized"], 2) == -0.10


def test_record_appends_and_thins(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    old = T0 - timedelta(days=10)
    with open(tmp_path / "equity.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=equity.FIELDS)
        w.writeheader()
        for k in range(12):  # 12 rows, 5 min apart, in one old hour
            w.writerow({"at": (old + timedelta(minutes=5 * k)).isoformat(), "mode": "paper", "realized": "0"})
    new = equity.record(T0, {})
    assert {r["mode"] for r in new} == {"paper", "live"}
    rows = list(csv.DictReader(open(tmp_path / "equity.csv")))
    assert len([r for r in rows if r["at"].startswith(old.strftime("%Y-%m-%dT%H"))]) == 1
    p = [r for r in rows if r["at"] == T0.isoformat(timespec="seconds") and r["mode"] == "paper"][0]
    assert "mentions:" in p["by_strategy"] and float(p["exposure"]) > 0
