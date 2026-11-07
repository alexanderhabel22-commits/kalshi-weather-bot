"""Replay the allocator on the historical trade streams (research_alloc/streams.py).

usage: python research_alloc/replay.py <streams.csv> [seeds] [key=value ...]
       (key=value overrides an [allocator] setting, e.g. prior_sd=0.05)

Each week every strategy takes a random sample of its historical fills at about
the live bot's pace (RATE per week, 1 contract each x its multiplier). The
allocator re-grades every Monday from the fills settled before that day, with
the live settings in config.toml [allocator]. Compares with fixed sizing:
  fixed     every strategy always 1x
  allocator multipliers from the weekly grades
  neutral   allocator rescaled so each week risks the same dollars as fixed
            (pure re-allocation: no extra money at risk)
"""
import csv
import random
import sys
from collections import defaultdict
from datetime import date, timedelta, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wxbot import allocator  # noqa: E402
from wxbot.util import load_config  # noqa: E402

RATE = {"mentions": 25, "favorites": 10, "macro_fade": 4, "weather": 50}
rows = list(csv.DictReader(open(sys.argv[1])))
seeds = int(sys.argv[2]) if len(sys.argv) > 2 else 20
cfg = load_config()
for kv in sys.argv[3:]:
    k, v = kv.split("=")
    cfg.raw.setdefault("allocator", {})[k] = float(v)
P = allocator.params(cfg)
by_week = defaultdict(lambda: defaultdict(list))
for r in rows:
    d = date.fromisoformat(r["date"])
    wk = d - timedelta(days=d.weekday())
    by_week[wk][r["strategy"]].append(r)
weeks = sorted(by_week)
weeks = [w for w in weeks if w >= date(2025, 2, 3)]
names = list(RATE)


def as_row(r, n):
    p = float(r["price"])
    return {"strategy": r["strategy"], "mode": "live", "event": r["event"], "ticker": r["ticker"],
            "price": r["price"], "filled": 1, "status": "settled",
            "pnl": f"{n * (int(r['won']) - p):.4f}", "fee": "", "settled_at": r["date"]}


def run(seed):
    rnd = random.Random(seed)
    hist, store = [], {}
    tot = {k: defaultdict(float) for k in ("fixed", "alloc", "neutral")}
    mult_log = []
    for wk in weeks:
        now = datetime(wk.year, wk.month, wk.day, 14, tzinfo=timezone.utc)
        a = allocator.update(cfg, hist, [], names, now, store)
        m = a["multipliers"]
        mult_log.append((wk, dict(m)))
        picks = {s: rnd.sample(by_week[wk][s], min(RATE[s], len(by_week[wk][s]))) for s in names}
        cost_fixed = sum(float(r["price"]) for s in names for r in picks[s])
        cost_alloc = sum(m[s] * float(r["price"]) for s in names for r in picks[s])
        k = cost_fixed / cost_alloc if cost_alloc else 1.0
        for s in names:
            for r in picks[s]:
                p, w = float(r["price"]), int(r["won"])
                for key, x in (("fixed", 1.0), ("alloc", m[s]), ("neutral", m[s] * k)):
                    tot[key][s + "_pnl"] += x * (w - p)
                    tot[key][s + "_cost"] += x * p
                hist.append(as_row(r, 1))   # the record is graded per dollar, size doesn't matter
    return tot, mult_log


agg = {k: defaultdict(list) for k in ("fixed", "alloc", "neutral")}
logs = []
for seed in range(seeds):
    tot, ml = run(seed)
    logs.append(ml)
    for key, t in tot.items():
        pnl = sum(v for k, v in t.items() if k.endswith("_pnl"))
        cost = sum(v for k, v in t.items() if k.endswith("_cost"))
        agg[key]["pnl"].append(pnl)
        agg[key]["roi"].append(pnl / cost)
        agg[key]["cost"].append(cost)
        for s in names:
            agg[key][s].append(t[s + "_pnl"])


def mean(x):
    return sum(x) / len(x)


print(f"{len(weeks)} weeks {weeks[0]}..{weeks[-1]}, {seeds} random samples, settings {P}")
for key in ("fixed", "alloc", "neutral"):
    a = agg[key]
    beat = sum(x > y for x, y in zip(a["pnl"], agg["fixed"]["pnl"])) if key != "fixed" else "-"
    print(f"{key:8s} profit ${mean(a['pnl']):7.2f}  risked ${mean(a['cost']):8.2f}  "
          f"per $1 {mean(a['roi'])*100:+.2f}c  beat fixed in {beat}/{seeds}  | " +
          "  ".join(f"{s} ${mean(a[s]):+.1f}" for s in names))
print("\nmultipliers (seed 0), every 8 weeks:")
for wk, m in logs[0][::8] + [logs[0][-1]]:
    print(" ", wk, "  ".join(f"{s} {m[s]:.2f}" for s in names))
