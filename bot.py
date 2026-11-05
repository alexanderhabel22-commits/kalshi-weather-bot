#!/usr/bin/env python3
"""Kalshi weather alert bot.

  python bot.py scan            one scan (what the scheduler runs)
  python bot.py scan --dry-run  scan and print alerts without sending or saving
  python bot.py loop            scan every few minutes (run on your own computer)
  python bot.py report          paper-trading record + last scan's model view
  python bot.py settle          grade finished alerts now
  python bot.py test-notify     send a test notification to your phone
  python bot.py backtest-fetch  download history for a backtest (--days, --out)
  python bot.py trade           automatic orders (mode in config [trading]; --dry-run prints only)
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

from wxbot import ledger, notify
from wxbot.scanner import LAST_SCAN, run_scan
from wxbot.util import Http, load_config, parse_iso, read_json, utcnow


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["scan", "loop", "report", "settle", "test-notify",
                                            "backtest-fetch", "trade"])
    ap.add_argument("--dry-run", action="store_true", help="don't send or save anything")
    ap.add_argument("--every", type=int, default=300, help="loop interval in seconds")
    ap.add_argument("--now", help="pretend it's this UTC time (ISO), for testing")
    ap.add_argument("--days", type=int, default=400, help="backtest-fetch: days of history")
    ap.add_argument("--out", default="backtest", help="backtest-fetch: output folder")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config()

    if args.command == "scan":
        out = run_scan(cfg, now=parse_iso(args.now) if args.now else None, dry_run=args.dry_run)
        print(f"{len(out['events'])} city-days priced, {len(out['sent'])} alerts"
              + (f", problems: {out['problems']}" if out["problems"] else ""))
        return 0

    if args.command == "trade":
        from wxbot import trader
        from wxbot import digest
        now = parse_iso(args.now) if args.now else utcnow()
        http = Http(cfg.user_agent)
        out = trader.run(cfg, now=now, http=http, dry_run=args.dry_run)
        try:
            for path in digest.run_due(cfg, now, http, dry_run=args.dry_run):
                print(f"report written: {path}")
        except Exception as exc:  # reports must never break trading
            logging.exception("reports failed: %s", exc)
        if not args.dry_run:
            try:
                from wxbot import quotes
                quotes.snapshot(http, now)
            except Exception as exc:  # dashboard prices must never break trading
                logging.exception("quotes failed: %s", exc)
        print(f"trade ({out['mode']}): placed {len(out['placed'])}, skipped {len(out['skipped'])}"
              + (f", stops: {out['stops']}" if out["stops"] else ""))
        return 0

    if args.command == "backtest-fetch":
        from pathlib import Path
        from wxbot import backtest_fetch
        for line in backtest_fetch.run(Path(args.out), args.days, cfg.user_agent):
            print(line)
        return 0

    if args.command == "loop":
        print(f"Scanning every {args.every}s. Ctrl+C to stop.")
        while True:
            try:
                out = run_scan(cfg, dry_run=args.dry_run)
                print(f"{utcnow():%H:%M:%S}Z  {len(out['events'])} city-days, {len(out['sent'])} alerts")
            except Exception as exc:  # keep the loop alive
                logging.exception("scan crashed: %s", exc)
            time.sleep(args.every)

    if args.command == "settle":
        rows = ledger.load()
        newly = ledger.settle(Http(cfg.user_agent), rows)
        ledger.save(rows)
        print(f"settled {len(newly)}; {ledger.record_line(rows)}")
        return 0

    if args.command == "report":
        from wxbot import report as reportmod, tracking
        from wxbot.scanner import RUNTIME
        text = reportmod.write_report(ledger.load(), tracking.load_census(), tracking.load_forecasts(),
                                      cfg, utcnow(), read_json(RUNTIME, {}))
        print(text)
        print("\n----- last scan -----")
        rows = ledger.load()
        s = ledger.stats(rows)
        print(ledger.record_line(rows))
        for kind in ("lock", "model"):
            k = ledger.stats(rows, kind=kind)
            if k["n"]:
                print(f"  {kind:5s}: {k['wins']}-{k['losses']}  pnl ${k['pnl']:+.2f}  roi {k['roi'] * 100:+.1f}%")
        if s["brier_model"] is not None:
            print(f"  Brier: model {s['brier_model']:.3f} vs market {s['brier_market'] or float('nan'):.3f}")
        print(f"  open: {s['open']} (${s['open_cost']:.2f})")
        sh = ledger.stats(rows, mode="shadow")
        if sh["n"] or sh["open"]:
            print(f"Silent EDGE test: {sh['wins']}-{sh['losses']}  pnl ${sh['pnl']:+.2f}  pending {sh['open']}")
            if sh["brier_model"] is not None and sh["brier_market"] is not None:
                print(f"  Brier: model {sh['brier_model']:.3f} vs market {sh['brier_market']:.3f}")
        last = read_json(LAST_SCAN, {})
        if last:
            print(f"\nLast scan {last.get('at')}:")
            for e in last.get("events", []):
                obs = "" if e["obs_extreme"] is None else f" obs {e['obs_extreme']:.1f}"
                print(f"  {e['event']:22s} {e['metric']:4s} median {e['median']}"
                      f" [{e['p10']}-{e['p90']}]{obs} nws {e['nws']}"
                      f" opps {len(e['opportunities'])}")
        return 0

    if args.command == "test-notify":
        ok = notify.send(Http(cfg.user_agent), cfg, "Weather bot test",
                         "If you can read this, alerts will reach your phone.", priority=3,
                         tags=["white_check_mark"], click="https://kalshi.com/category/climate")
        print("sent" if ok else "NOT sent (is NTFY_TOPIC set?)")
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
