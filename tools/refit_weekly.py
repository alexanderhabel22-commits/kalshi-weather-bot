"""Weekly refit of the weather model's per-city settings (model weights, biases,
error sd) from the newest settled days.

usage: python tools/refit_weekly.py <backtest root> [params.json]

For each series already in data/daybefore_params.json (new cities are never
added here, so this can't put real money on a new market):
  1. test: fit on the 45 days before the last 7, score the last 7 days
     (mean abs error of the combined forecast) against the current settings;
  2. if the fresh fit is no worse (+0.1F slack) and sane, refit on the latest
     45 days and keep it; otherwise keep the current settings.
Done for the day-before settings ("series", lead d1) and the same-day exit
settings ("exit.series", lead d0). Blend coefficients are left alone.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, '.')
from wxbot import backtest as bt  # noqa: E402

WINDOW, TEST, SLACK = 45, 7, 0.1


def mae(days, p, lead):
    errs = [abs(d.actual - c) for d in days
            if d.actual is not None and (c := bt.combine(d.fc, p["weights"], p["bias"], lead)) is not None]
    return (sum(errs) / len(errs), len(errs)) if errs else (None, 0)


def fit(days, lead, models):
    f = bt.fit_city(days, lead, models)
    if f is None:
        return None
    w, bias, a, _ = f
    return {"weights": {m: round(v, 4) for m, v in w.items()},
            "bias": {m: round(v, 3) for m, v in bias.items()},
            "sd": round(max(a ** 0.5, 1.2), 3), "days": len(days)}


def sane(p):
    return p and 1.2 <= p["sd"] <= 6 and all(abs(b) < 10 for b in p["bias"].values()) and p["days"] >= 20


def refit(by, old_series, lead, last):
    out, log = {}, []
    for s, old in old_series.items():
        L = [d for d in by.get(s, []) if d.actual is not None]
        models = sorted(old["weights"])
        test = [d for d in L if (last - d.day).days < TEST]
        train = [d for d in L if TEST <= (last - d.day).days < TEST + WINDOW]
        cand = fit(train, lead, models)
        m_old, n = mae(test, old, lead)
        m_new, _ = mae(test, cand, lead) if cand else (None, 0)
        if cand and m_old is not None and m_new is not None and n >= 4 and m_new <= m_old + SLACK:
            new = fit([d for d in L if (last - d.day).days < WINDOW], lead, models)
            if sane(new):
                out[s] = new
                log.append(f"{s} {lead}: refit (last {n} days MAE {m_old:.2f} -> {m_new:.2f})")
                continue
        out[s] = old
        log.append(f"{s} {lead}: kept (last {n} days MAE old {m_old or 0:.2f}, fresh fit {m_new or 0:.2f})")
    return out, log


def main(root, path):
    days = bt.load(Path(root), hours=(36,))
    last = max(d.day for d in days if d.actual is not None)
    by = {}
    for d in days:
        by.setdefault(d.series, []).append(d)
    P = json.load(open(path))
    P["series"], log1 = refit(by, P["series"], "d1", last)
    lines = log1
    if (P.get("exit") or {}).get("series"):
        P["exit"]["series"], log2 = refit(by, P["exit"]["series"], "d0", last)
        lines += log2
    from datetime import date
    P["refit_at"] = str(last)
    P["refit_run"] = date.today().isoformat()
    json.dump(P, open(path, "w"), indent=1)
    print("\n".join(lines))
    print(f"refit {sum('refit (' in x for x in lines)} / {len(lines)} through {last}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "data/daybefore_params.json")
