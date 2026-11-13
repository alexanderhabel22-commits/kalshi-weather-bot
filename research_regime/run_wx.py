import sys, pandas as pd
sys.path.insert(0, "research_regime")
from regime import add_form, search, streak_stats
FEATS = ["px", "spread", "metric", "model_gap", "mkt_p", "sd", "dow", "form20", "form100", "last_win", "loss_streak", "yday"]
d = pd.read_pickle(sys.argv[1])
d["cl"] = d.day
g = add_form(d)
cd, info = search(g, FEATS, "cl")
st = streak_stats(g)
pd.set_option("display.width", 250)
print({k: v for k, v in info.items() if k != "pick"}); print("pick", info.get("pick")); print(st)
sig = cd[cd.gap_t >= 2]
print(sig[["feature", "filter", "cut1", "cut2", "keep_tr", "train", "test", "test_se", "keep_te"]].round(2).to_string(index=False))
print("monthly:", g.groupby(g.day.str[:7]).pnl.agg(["mean", "size"]).round(1).T.to_string())
cd.to_pickle(sys.argv[2])
