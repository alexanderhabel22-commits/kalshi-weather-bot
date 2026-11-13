"""Fixed (not fitted) on/off switches driven by a strategy's own recent results, on every ledger.
Whole sample is fine here because nothing is tuned: the rules are the textbook ones."""
import sys, datetime, zoneinfo, pandas as pd
sys.path.insert(0, "research_regime")
from regime import add_form, clustered
ET = zoneinfo.ZoneInfo("America/New_York")
W = sys.argv[1]
L = {}
m15 = pd.read_pickle(f"{W}/m15.pkl")
for (s, r), g in m15.groupby(["series", "rule"]):
    g = g.copy(); g["cl"] = g.day; L[f"{s.replace('KX','').replace('15M','')}:{r}"] = g
for n in ("mentions", "favorites", "macro"):
    d = pd.read_pickle(f"{W}/main_{n}.pkl"); d = d[(d.kind == "live") & d.touch.notna()].copy()
    d["t"] = d.t0.astype(int); d["close"] = d.close_ts.astype(int)
    d["pnl"] = d.pnl - 100 * 0.0175 * d.px * (1 - d.px)
    d["day"] = d.t.map(lambda x: datetime.datetime.fromtimestamp(x, ET).date().isoformat()); d["cl"] = d.event
    L[n] = d
w = pd.read_pickle(f"{W}/wx_ledger.pkl"); w["cl"] = w.day; L["weather_daybefore"] = w
RULES = {"last20>0": lambda g: g.form20 > 0, "last100>0": lambda g: g.form100 > 0,
         "yesterday>0": lambda g: g.yday > 0, "pause after 3 losses": lambda g: g.loss_streak < 3,
         "last20<0 (contrarian)": lambda g: g.form20 < 0}
rows = []
for k, g in L.items():
    g = add_form(g)
    g = g[g.form100.notna() & g.yday.notna()]
    base = clustered(g.pnl.values, g.cl.values)
    row = dict(strategy=k, n=len(g), always_on=f"{base[0]:+.1f}±{base[1]:.1f}")
    for rn, f in RULES.items():
        mk = f(g).values
        on = clustered(g.pnl.values[mk], g.cl.values[mk]); off = clustered(g.pnl.values[~mk], g.cl.values[~mk])
        row[rn] = f"{on[0]:+.1f}|{off[0]:+.1f} ({mk.mean():.0%})"
    rows.append(row)
pd.set_option("display.width", 300); pd.set_option("display.max_columns", 20)
print("cells: c/ct when switch says ON | when it says OFF (share ON)")
print(pd.DataFrame(rows).to_string(index=False))
