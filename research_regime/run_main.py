"""Regime search on the main-bot replay ledgers (research_main/replay.py output)."""
import sys, datetime, zoneinfo, pandas as pd, numpy as np
sys.path.insert(0, "research_regime")
from regime import add_form, search, streak_stats
ET = zoneinfo.ZoneInfo("America/New_York")
FEATS = ["px", "hours", "vol", "ev_vol", "spread", "joined", "hour", "dow",
         "form20", "form100", "last_win", "loss_streak", "yday"]
allc, summ = [], []
for p in sys.argv[2:]:
    name = p.split("main_")[-1].split(".")[0]
    d = pd.read_pickle(p)
    d = d[(d.kind == "live") & d.touch.notna()].copy()        # orders the rule places that fill
    d["t"] = d.t0.astype(int)
    d["close"] = d.close_ts.astype(int)
    fee = 100 * 0.0175 * d.px * (1 - d.px)
    d["pnl"] = d.pnl - fee
    dt = d.t.map(lambda x: datetime.datetime.fromtimestamp(x, ET))
    d["hour"] = dt.map(lambda x: x.hour); d["dow"] = dt.map(lambda x: x.weekday())
    d["day"] = dt.map(lambda x: x.date().isoformat())
    d["spread"] = d.ask - d.bid
    g = add_form(d)
    cd, info = search(g, FEATS, "event")
    cd["strategy"] = name
    allc.append(cd)
    st = streak_stats(g)
    pk = info.get("pick")
    summ.append(dict(strategy=name, n_tr=info["n_train"], n_te=info["n_test"], test_from=info["test_span"][0],
                     base_tr=round(info["base_train"][0], 2), base_te=round(info["base_test"][0], 2),
                     base_te_se=round(info["base_test"][1], 2),
                     cands=info["n_candidates"], sig=info.get("n_sig_train"),
                     sign_kept=round(info.get("sign_kept", np.nan), 2), corr=round(info.get("corr", np.nan), 2),
                     pick=f"{pk['feature']}:{pk['filter']} ({pk['cut1']},{pk['cut2']})" if pk else "-",
                     pick_tr=round(pk["train"], 2) if pk else None, pick_te=round(pk["test"], 2) if pk else None,
                     pick_te_se=round(pk["test_se"], 2) if pk else None,
                     pick_keep_te=round(pk["keep_te"], 2) if pk else None,
                     day_ac=round(st["day_autocorr"], 2), form20_corr=round(st["form20_corr"], 3)))
cd = pd.concat(allc); cd.to_pickle(sys.argv[1])
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 30)
print(pd.DataFrame(summ).to_string(index=False))
sig = cd[cd.gap_t >= 2]
print("candidates", len(cd), "| train-positive kept sign", round((cd[cd.train_lift > 0].test_lift > 0).mean(), 3),
      "| train-significant", len(sig), "kept", round((sig.test_lift > 0).mean(), 3),
      "train lift", round(sig.train_lift.mean(), 2), "holdout lift", round(sig.test_lift.mean(), 2))
print(sig.sort_values(["strategy", "train"], ascending=[True, False])[
    ["strategy", "feature", "filter", "cut1", "cut2", "keep_tr", "train", "test", "test_se", "keep_te"]].round(2).to_string(index=False))
