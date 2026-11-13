"""One full scan: settle old alerts, price every open weather market, alert."""
from __future__ import annotations

import logging
import zlib
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import daybefore, kalshi, latepaper, launches, ledger, moneysum, notify, rain, report as reportmod, strategy, tracking, trader
from .model import build_day_model
from .stations import (SeriesInfo, Station, cli_window, climate_day_of,
                       default_catalog, series_from_kalshi)
from .util import (CACHE_DIR, DATA_DIR, Config, Http, HttpError, parse_iso, read_json,
                   utcnow, write_json)
from .weather import fetch_ensemble, fetch_nws_extremes, fetch_observations

log = logging.getLogger("wxbot")

STATE = DATA_DIR / "state.json"
RUNTIME = CACHE_DIR / "runtime.json"
LAST_SCAN = CACHE_DIR / "last_scan.json"
LOCK_SIGNALS = DATA_DIR / "lock_signals.json"


# ---------------------------------------------------------------- catalog

def get_catalog(http: Http, cfg: Config, state: dict, now: datetime) -> list[SeriesInfo]:
    today = now.date().isoformat()
    cat = state.get("catalog") or {}
    series: list[SeriesInfo] = []
    cached = {s.get("ticker") for s in (cat.get("series") or [])}
    fresh = cat.get("date") == today and cat.get("series")
    if fresh and not {si.ticker for si in default_catalog()} <= cached:
        fresh = False                      # the built-in list grew (new code): re-discover now
    if fresh:
        for s in cat["series"]:
            st = Station(s["icao"], s["name"], s["lat"], s["lon"], s["tz"])
            series.append(SeriesInfo(s["ticker"], s["metric"], st, s["city"]))
    elif cfg.get("markets", "auto_discover", True):
        try:
            raw = kalshi.list_weather_series(http)
        except Exception as exc:  # discovery is best-effort
            log.info("series discovery failed: %s", exc)
            raw = []
        found = {}
        for r in raw:
            si = series_from_kalshi(r, http)
            if si:
                found[si.ticker] = si
        if len(found) >= 5:
            # keep defaults that discovery missed (if Kalshi's category moved)
            for si in default_catalog():
                found.setdefault(si.ticker, si)
            series = list(found.values())
            state["catalog"] = {"date": today, "series": [
                {"ticker": s.ticker, "metric": s.metric, "city": s.city, "icao": s.station.icao,
                 "name": s.station.name, "lat": s.station.lat, "lon": s.station.lon,
                 "tz": s.station.tz} for s in series]}
    if not series:
        series = default_catalog()

    only = [t.upper() for t in (cfg.get("markets", "series", []) or [])]
    if only:
        series = [s for s in series if s.ticker in only]
    if not cfg.get("markets", "include_lows", True):
        series = [s for s in series if s.metric == "high"]
    skip = {c.lower() for c in (cfg.get("markets", "skip_cities", []) or [])}
    return [s for s in series if s.city.lower() not in skip]


# ---------------------------------------------------------------- helpers

def _should_alert(state: dict, opp, now: datetime, gain: float) -> bool:
    prev = (state.get("alerted") or {}).get(opp.key)
    if not prev:
        return True
    if opp.kind in ("lock", "near") and prev.get("kind") != opp.kind:
        return True
    last = parse_iso(prev.get("at")) or now
    return opp.edge >= float(prev.get("edge", 0)) + gain and now - last >= timedelta(minutes=60)


def _prune(state: dict, now: datetime) -> None:
    alerted = state.get("alerted") or {}
    cutoff = now - timedelta(days=4)
    state["alerted"] = {k: v for k, v in alerted.items()
                        if (parse_iso(v.get("at")) or now) >= cutoff}
    daily = state.get("daily") or {}
    keep = sorted(daily)[-14:]
    state["daily"] = {k: daily[k] for k in keep}


def _in_quiet(hour: int, start: int, end: int) -> bool:
    if start == end:
        return False
    return start <= hour < end if start < end else (hour >= start or hour < end)


def lock_signal(opp, dm, close: str, now: datetime) -> dict:
    """What the trader needs to buy a LOCK (wxbot/strats/weather_lock.py)."""
    return {"ticker": opp.ticker, "event": opp.event, "side": opp.side, "city": opp.city,
            "metric": opp.metric, "date": opp.date, "bracket": opp.label,
            "price": opp.price, "fair_p": round(opp.fair_p, 4), "close": close,
            "obs_extreme": None if dm.obs_extreme is None else round(dm.obs_extreme, 1),
            "guaranteed": dm.guaranteed, "seen_at": now.isoformat(timespec="seconds")}


def save_lock_signals(signals: list[dict], now: datetime) -> None:
    """Every LOCK this scan found (quiet hours and alert limits don't apply).
    Rewritten each scan; left alone when empty before and after (no commit noise)."""
    old = read_json(LOCK_SIGNALS, {}) or {}
    if not signals and not old.get("signals"):
        return
    write_json(LOCK_SIGNALS, {"at": now.isoformat(timespec="seconds"), "signals": signals})


# ---------------------------------------------------------------- scan

def run_scan(cfg: Config, *, now: datetime | None = None, dry_run: bool = False,
             http: Http | None = None) -> dict:
    now = now or utcnow()
    http = http or Http(cfg.user_agent)
    zone = ZoneInfo(cfg.tz)
    local = now.astimezone(zone)
    local_day = local.date().isoformat()

    state = read_json(STATE, {}) or {}
    runtime = read_json(RUNTIME, {}) or {}
    rows = ledger.load()
    census = tracking.load_census()
    frows = tracking.load_forecasts()
    fblocks = tracking.forecast_blocks(frows)
    quotes: dict[str, tuple] = {}
    problems: list[str] = []
    sent: list[dict] = []
    report: list[dict] = []

    mparams = cfg.raw.get("model", {})
    sparams = cfg.strategy_params
    aparams = cfg.raw.get("alerts", {})
    bankroll = cfg.bankroll
    max_ahead = int(cfg.get("markets", "max_days_ahead", 1))

    # 1) grade any finished alerts
    try:
        newly = ledger.settle(http, rows, now)
        if newly:
            log.info("settled %d alerts", len(newly))
    except Exception as exc:
        problems.append(f"settling failed: {exc}")
    try:
        tracking.settle_forecasts(http, frows, now)
    except Exception as exc:
        log.warning("grading forecasts failed: %s", exc)

    # 2) what to scan
    catalog = get_catalog(http, cfg, state, now)

    station_obs: dict[str, object] = {}
    station_ens: dict[str, object] = {}
    station_nws: dict[str, dict] = {}
    kalshi_ok = kalshi_fail = obs_fail = 0
    daily = state.setdefault("daily", {}).setdefault(local_day, {"alerts": 0})
    max_per_day = int(aparams.get("max_alerts_per_day", 15))
    quiet = _in_quiet(local.hour, int(aparams.get("quiet_start", 0)), int(aparams.get("quiet_end", 7)))
    quiet_locks_ok = bool(aparams.get("quiet_allows_locks", False))
    gain = float(aparams.get("realert_edge_gain", 0.05))
    edge_mode = str(aparams.get("edge_alerts", "shadow")).lower()   # off | shadow | on
    modes = {"model": edge_mode,                                       # EDGE calls
             "near": str(aparams.get("near_lock_alerts", "shadow")).lower()}
    candidates = []
    lock_signals: list[dict] = []     # every LOCK this scan, for the trader (data/lock_signals.json)
    shadow_logged = 0
    tomorrow: dict[str, tuple] = {}
    today_events: dict[str, tuple] = {}   # for the late-day paper test

    for si in catalog:
        st = si.station
        try:
            markets = kalshi.list_open_markets(http, si.ticker)
            kalshi_ok += 1
        except HttpError as exc:
            kalshi_fail += 1
            log.warning("markets %s: %s", si.ticker, exc)
            if kalshi_ok == 0 and kalshi_fail >= 3:
                break   # Kalshi is down; don't burn the whole run retrying
            continue
        by_event: dict[str, list[dict]] = defaultdict(list)
        for m in markets:
            quotes[m.get("ticker", "")] = (kalshi.price(m, "yes_ask"), kalshi.price(m, "no_ask"))
            close = parse_iso(m.get("close_time"))
            if close and close <= now:
                continue
            by_event[m.get("event_ticker", "")].append(m)

        today_cli = climate_day_of(now, st.tz)
        for ev, ms in by_event.items():
            d = kalshi.event_date(ev)
            if d is None:
                continue
            ahead = (d - today_cli).days
            if ahead < 0 or ahead > max_ahead:
                continue
            ws, we = cli_window(d, st.tz)
            if now >= we:
                continue
            if ahead == 1:
                tomorrow[ev] = (si, ms)

            # station data, fetched once per station per scan
            if st.icao not in station_obs:
                start = cli_window(today_cli, st.tz)[0] - timedelta(hours=7)
                try:
                    station_obs[st.icao] = fetch_observations(http, st.icao, start, now)
                except HttpError as exc:
                    obs_fail += 1
                    log.warning("observations %s: %s", st.icao, exc)
                    station_obs[st.icao] = None
                station_ens[st.icao] = fetch_ensemble(
                    http, st, mparams.get("ensemble_models", "gfs025,ecmwf_ifs025,icon_seamless,gem_global"),
                    int(mparams.get("forecast_cache_minutes", 90)))
                station_nws[st.icao] = fetch_nws_extremes(http, st)

            obs, ens = station_obs[st.icao], station_ens[st.icao]
            if ahead == 0 and obs is not None:
                today_events[ev] = (si, ms, obs, ws)
            if ens is None and ahead > 0:
                continue
            nws_val = (station_nws.get(st.icao) or {}).get(d.isoformat(), {}).get(si.metric)
            seed = zlib.crc32(f"{ev}|{now:%Y%m%d%H}".encode())
            dm = build_day_model(si.metric, ws, we, now, obs, ens, nws_val, mparams, seed)
            if dm is None:
                continue
            log.info("model %s %s: median %s [%s-%s] nws %s obs %s (%s, %d six-hour groups) "
                     "bias %+.1f sigma %.1f members %d",
                     ev, si.metric, dm.median, dm.quantile(0.1), dm.quantile(0.9),
                     None if nws_val is None else round(nws_val), None if dm.obs_extreme is None
                     else round(dm.obs_extreme, 1), dm.obs_source or "-", dm.n_six,
                     dm.bias, dm.sigma, dm.members)
            locks_only = ens is None or bool(dm.notes)   # missing forecast / readings
            opps, rows_eval = strategy.evaluate_event(
                ms, dm, series=si.ticker, city=si.city, metric=si.metric,
                date=d.isoformat(), day_ahead=ahead > 0, params=sparams, locks_only=locks_only)
            tracking.census_update(census, local_day, ev, si.city, si.metric, rows_eval, now)
            tracking.log_forecast(frows, fblocks, event=ev, series=si.ticker, city=si.city,
                                  metric=si.metric, date=d.isoformat(), dm=dm, nws=nws_val,
                                  window_end=we, now=now, sample_ticker=ms[0].get("ticker", ""))
            report.append({"event": ev, "city": si.city, "metric": si.metric,
                           "median": dm.median, "p10": dm.quantile(0.1), "p90": dm.quantile(0.9),
                           "obs_extreme": dm.obs_extreme, "guaranteed": dm.guaranteed,
                           "nws": nws_val, "bias": round(dm.bias, 2), "sigma": round(dm.sigma, 2),
                           "members": dm.members, "notes": dm.notes, "brackets": rows_eval,
                           "opportunities": [{"ticker": o.ticker, "side": o.side, "kind": o.kind,
                                              "price": o.price, "edge": round(o.edge, 4)} for o in opps]})
            for o in opps:
                candidates.append((o, dm, si))
            closes = {m.get("ticker"): m.get("close_time") or "" for m in ms}
            lock_signals += [lock_signal(o, dm, closes.get(o.ticker, ""), now)
                             for o in opps if o.kind == "lock"]

    # 3) alert on the best candidates (locks first, then biggest edge),
    #    at most one new alert per city-day per scan
    candidates.sort(key=lambda c: (c[0].kind != "lock", -c[0].edge))
    events_done: set[str] = set()
    shadow_events: set[tuple[str, str]] = set()
    for opp, dm, si in candidates:
        mode = modes.get(opp.kind, "on")
        if mode != "on":
            # Forecast calls are unproven: log them silently and grade them,
            # but don't send them ("shadow"), or ignore them ("off").
            # One silent call per city-day per kind per scan.
            if (mode == "shadow" and (opp.kind, opp.event) not in shadow_events
                    and _should_alert(state, opp, now, gain)):
                strategy.size(opp, bankroll, 0.0, 0.0, None, sparams)
                if opp.contracts > 0:
                    ledger.record(rows, opp, dm, now, mode="shadow")
                    state.setdefault("alerted", {})[opp.key] = {
                        "edge": round(opp.edge, 4), "price": opp.price, "kind": opp.kind,
                        "shadow": True, "at": now.isoformat(timespec="seconds")}
                    shadow_events.add((opp.kind, opp.event))
                    shadow_logged += 1
            continue
        if daily["alerts"] >= max_per_day:
            continue
        if opp.event in events_done:
            continue
        if not _should_alert(state, opp, now, gain):
            continue
        if quiet and not (quiet_locks_ok and opp.kind == "lock"):
            continue
        book = kalshi.get_orderbook(http, opp.ticker)
        depth = kalshi.depth_to_buy(book, opp.side, opp.price) if book else None
        strategy.size(opp, bankroll, ledger.open_exposure(rows), ledger.open_exposure(rows, opp.event),
                      depth, sparams)
        if opp.contracts <= 0 or "expected profit below minimum" in opp.notes:
            continue
        title, body = notify.format_trade(opp, dm, si.station.name, si.station.tz, bankroll,
                                          ledger.record_line(rows), now)
        delivered = notify.send(http, cfg, title, body, priority=4 if opp.kind == "lock" else 3,
                                tags=["lock" if opp.kind == "lock" else "chart_with_upwards_trend"],
                                click=kalshi.market_url(opp.ticker), dry_run=dry_run)
        if not delivered and not dry_run:
            problems.append("alert not delivered (is NTFY_TOPIC set?)")
            continue   # don't log or dedupe an alert you never saw; retry next scan
        ledger.record(rows, opp, dm, now)
        state.setdefault("alerted", {})[opp.key] = {
            "edge": round(opp.edge, 4), "price": opp.price, "kind": opp.kind,
            "at": now.isoformat(timespec="seconds")}
        daily["alerts"] += 1
        events_done.add(opp.event)
        sent.append({"title": title, "body": body})

    ledger.track_prices(rows, quotes, now)

    # paper trading: day-before limit orders (no money, never alerts)
    paper = None
    if cfg.get("markets", "paper_daybefore", True):
        paper = daybefore.load_orders()
        try:
            n_paper = daybefore.place(http, paper, tomorrow, now, state)
            if n_paper:
                log.info("paper orders placed: %d", n_paper)
            if now.minute % 30 < 5:                   # results come hours after close
                daybefore.settle(http, paper, now)
        except Exception as exc:  # never let the test break the scan
            log.warning("paper trading failed: %s", exc)

    # paper test: late-day orders on today's markets (no money, never alerts)
    late = None
    if cfg.get("markets", "paper_late", True):
        late = latepaper.load_orders()
        try:
            n_late = latepaper.place(late, today_events, now, cfg.raw.get("paper_late"))
            if n_late:
                log.info("late-day paper orders written: %d", n_late)
            if now.minute % 30 < 5:
                latepaper.settle(http, late, now)
        except Exception as exc:  # never let the test break the scan
            log.warning("late-day paper test failed: %s", exc)

    # silent rain census (no alerts), every 15 minutes
    rain_census = None
    if cfg.get("markets", "rain_census", True) and now.minute % 15 < 5:
        rain_census = rain.load()
        try:
            rain.census_scan(http, state, rain_census, now)
        except Exception as exc:  # never let the test break the scan
            log.warning("rain census failed: %s", exc)

    # new-market watcher (silent unless launch_alerts = "on"), every 30 minutes
    launch_db = None
    if cfg.get("markets", "launch_watch", True) and now.minute % 30 < 5:
        launch_db = launches.load()
        try:
            launches.watch(http, cfg, launch_db, now, quiet=quiet, dry_run=dry_run)
        except Exception as exc:  # never let the watcher break the scan
            log.warning("launch watch failed: %s", exc)

    # 4) health checks
    if catalog and kalshi_ok == 0:
        problems.append("could not reach Kalshi")
    if station_obs and obs_fail == len(station_obs):
        problems.append("could not reach NWS observations")
    if any(v is None for v in station_ens.values()):
        missing = [k for k, v in station_ens.items() if v is None]
        problems.append(f"no forecast data for {', '.join(missing[:6])}")
    fails = runtime.get("consecutive_failures", 0)
    runtime["consecutive_failures"] = fails + 1 if (catalog and kalshi_ok == 0) else 0
    last_err = parse_iso(state.get("last_error_alert"))
    if runtime["consecutive_failures"] >= 3 and (not last_err or now - last_err > timedelta(hours=6)):
        if notify.send(http, cfg, "Weather bot problem",
                       "The bot has failed 3 scans in a row: " + "; ".join(problems),
                       priority=4, tags=["warning"], dry_run=dry_run):
            state["last_error_alert"] = now.isoformat(timespec="seconds")

    scans = runtime.setdefault("scans", {})
    scans[local_day] = scans.get(local_day, 0) + 1
    runtime["scans"] = {k: scans[k] for k in sorted(scans)[-7:]}
    probs_today = runtime.setdefault("problems", {})
    if problems:
        probs_today[local_day] = sorted(set(probs_today.get(local_day, []) + problems))[:10]
    runtime["problems"] = {k: probs_today[k] for k in sorted(probs_today)[-3:]}

    # 5) daily summary (doubles as a heartbeat)
    if local.hour >= int(aparams.get("summary_hour", 21)) and state.get("last_summary") != local_day:
        midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
        old = ledger.stats(rows)
        alert_line = (f"Weather alerts (old, no money): {daily['alerts']} sent today, "
                      f"record {old['wins']}-{old['losses']}") if daily["alerts"] or old["n"] else ""
        try:
            title, body = moneysum.format_summary(trader.load_all(), midnight, alert_line,
                                                  runtime["problems"].get(local_day, []))
        except Exception as exc:  # the heartbeat must still go out
            log.warning("money summary failed: %s", exc)
            title, body = notify.format_summary(
                old, ledger.stats(rows, since=midnight), daily["alerts"], scans[local_day],
                sum(r.get("status") == "open" and not ledger.is_shadow(r) for r in rows),
                runtime["problems"].get(local_day, []))
        url = reportmod.report_url()
        if url:
            body += "\nTap for the full report."
        if not dry_run:
            reportmod.write_report(rows, census, frows, cfg, now, runtime)
        if trader.settings(cfg)["mode"] != "off":
            # the trade run sends the money summary with freshly synced Kalshi data
            # (digest.run_money_summary); the scan only writes the report
            state["last_summary"] = local_day
        elif notify.send(http, cfg, title, body, priority=2, tags=["bar_chart"], click=url,
                         dry_run=dry_run):
            state["last_summary"] = local_day

    _prune(state, now)
    if not dry_run:
        save_lock_signals(lock_signals, now)
        ledger.save(rows)
        write_json(STATE, state)
        tracking.save_census(census)
        tracking.save_forecasts(frows, now=now)
        reportmod.ensure_my_trades_file()
        if rain_census is not None:
            rain.save(rain_census)
        if launch_db is not None:
            launches.save(launch_db)
        if paper is not None:
            daybefore.save_orders(paper)
        if late is not None:
            latepaper.save_orders(late)
    write_json(RUNTIME, runtime)
    write_json(LAST_SCAN, {"at": now.isoformat(), "events": report, "problems": problems})
    log.info("scan done: %d series, %d events, %d alerts, %d silent EDGE logged, problems=%s",
             len(catalog), len(report), len(sent), shadow_logged, problems)
    return {"events": report, "sent": sent, "problems": problems, "shadow": shadow_logged}
