"""Day-before weather ledger: the live recipe's walk-forward replay (research_wx/sim.py),
one row per filled order, with features known at order time.
  python research_regime/ledger_wx.py data.pkl.rows36.pkl out.pkl"""
import pickle, sys, datetime
import numpy as np, pandas as pd
sys.path.insert(0, "research_wx")
import sim
rows = pickle.load(open(sys.argv[1], "rb"))
KEYS = ["gfs_seamless.d1", "ecmwf_ifs025.d1", "icon_seamless.d1", "gem_seamless.d1"]
sim.combine_invmse(rows, KEYS)
B = sim.brackets(rows, 36)
tr, _ = sim.walk(B, 0.06)
out = []
for day, side, px, pnl, r, f in tr:
    d = r["d"] if "d" in r else {}
    out.append(dict(day=str(day), t=int(datetime.datetime.combine(day, datetime.time()).timestamp()),
                    side=side, px=px, pnl=100 * pnl, win=int(pnl > 0),
                    spread=r["a"] - r["b"], metric=1 if r["metric"] == "high" else 0,
                    model_gap=(r["f"] - r["m"]) * (1 if side == "yes" else -1), mkt_p=r["pm"],
                    sd=r["row"].get("sd", np.nan), city=r.get("city", ""), dow=day.weekday()))
df = pd.DataFrame(out)
df["close"] = df.t + 2 * 86400          # settles the morning after the day; placed ~1.5 days before
df["t"] = df.t - 36 * 3600
df.to_pickle(sys.argv[2]); print(len(df), df.day.min(), df.day.max(), round(df.pnl.mean(), 2))
print(df.head())
