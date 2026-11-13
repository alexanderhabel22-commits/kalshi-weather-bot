import sys, pandas as pd, numpy as np
sys.path.insert(0, "research_regime")
from regime import add_form, search, streak_stats
FEATS = ["px", "left", "spread", "mvol", "rv2h", "trend1h", "trend24h", "absret1h", "hour", "dow",
         "form20", "form100", "last_win", "loss_streak", "yday"]
df = pd.read_pickle(sys.argv[1])
allc, summ = [], []
for (s, r), g in df.groupby(["series", "rule"]):
    g = add_form(g)
    cd, info = search(g, FEATS, "day")
    cd["strategy"] = f"{s}:{r}"
    allc.append(cd)
    st = streak_stats(g)
    p = info.get("pick")
    summ.append(dict(strategy=f"{s}:{r}", n_tr=info["n_train"], n_te=info["n_test"],
                     test_from=info["test_span"][0],
                     base_tr=round(info["base_train"][0], 2), base_te=round(info["base_test"][0], 2),
                     cands=info["n_candidates"], sig=info.get("n_sig_train"),
                     sign_kept=round(info.get("sign_kept", np.nan), 2), corr=round(info.get("corr", np.nan), 2),
                     pick=f"{p['feature']}:{p['filter']} ({p['cut1']},{p['cut2']})" if p else "-",
                     pick_tr=round(p["train"], 2) if p else None,
                     pick_te=round(p["test"], 2) if p else None, pick_te_se=round(p["test_se"], 2) if p else None,
                     pick_keep_te=round(p["keep_te"], 2) if p else None,
                     day_ac=round(st["day_autocorr"], 2), form20_corr=round(st["form20_corr"], 3)))
cd = pd.concat(allc)
cd.to_pickle(sys.argv[2])
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 30)
print(pd.DataFrame(summ).to_string(index=False))
print("total candidates", len(cd))
pos = cd[cd.train_lift > 0]
print("ALL: train-positive lifts that stayed positive on holdout:", round((pos.test_lift > 0).mean(), 3), "of", len(pos))
sig = cd[cd.gap_t >= 2]
print("train-significant (t>=2):", len(sig), "kept sign:", round((sig.test_lift > 0).mean(), 3),
      "mean train lift", round(sig.train_lift.mean(), 2), "mean holdout lift", round(sig.test_lift.mean(), 2))
print("by feature (train-significant only):")
print(sig.groupby("feature").agg(n=("test_lift", "size"), tr=("train_lift", "mean"), te=("test_lift", "mean"),
                                 kept=("test_lift", lambda x: (x > 0).mean())).round(2).to_string())
