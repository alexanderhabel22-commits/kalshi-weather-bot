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
  python bot.py srclog          shadow logger: settlement-source changes vs Kalshi prices (server; logs only)
  python bot.py paperlab       paper tests: wide-spread market making + parlay quote requests (server; no orders)
  python bot.py lip            liquidity-reward test: cheap resting bids in reward markets (server; lip_control.json)
  python bot.py copycat        shadow logger: public trades of proven winners vs later prices (server; logs only)
  python bot.py m15             always-on 15-minute fade bot (runs on the server; --once = one step)
  python bot.py watch           AI ops watcher, one pass (server timer every 15 min; needs ANTHROPIC_API_KEY)
  python bot.py mentions-ai     Mentions AI shadow test: would skipping high "will be said" scores have helped?
  python bot.py checkpoint      real-money scorecard + pass/fail verdicts (rules in CHECKPOINT.md)
  python bot.py tax             tax-ready ledger of every real fill, fee and settlement (read-only)
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
                                            "backtest-fetch", "trade", "m15", "checkpoint", "watch", "mentions-ai",
                                            "tax", "srclog", "copycat", "lip", "paperlab"])
    ap.add_argument("--dry-run", action="store_true", help="don't send or save anything")
    ap.add_argument("--every", type=int, default=300, help="loop interval in seconds")
    ap.add_argument("--now", help="pretend it's this UTC time (ISO), for testing")
    ap.add_argument("--days", type=int, default=400, help="backtest-fetch: days of history")
    ap.add_argument("--out", default="backtest", help="backtest-fetch: output folder")
    ap.add_argument("--recent-only", action="store_true", help="backtest-fetch: skip the historical archive and MOS")
    ap.add_argument("--once", action="store_true", help="m15: one step, then exit")
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

    if args.command == "mentions-ai":
        from wxbot import mentions_ai
        return mentions_ai.main()

    if args.command == "watch":
        from wxbot import watch
        return watch.main(cfg)

    if args.command == "srclog":
        from wxbot import srclog
        return srclog.main()

    if args.command == "paperlab":
        from wxbot import paperlab
        return paperlab.main()

    if args.command == "lip":
        from wxbot import lip
        return lip.main()

    if args.command == "copycat":
        from wxbot import copycat
        return copycat.main()

    if args.command == "m15":
        from wxbot import m15
        return m15.main(cfg, once=args.once)

    if args.command == "trade":
        from wxbot import trader
        from wxbot import digest
        now = parse_iso(args.now) if args.now else utcnow()
        http = Http(cfg.user_agent)
        if cfg.get("account", "auto_bankroll", True):
            from wxbot import balance
            balance.refresh(http, now)       # bankroll follows the Kalshi balance
        out = trader.run(cfg, now=now, http=http, dry_run=args.dry_run)   # syncs fills + settlements
        if not args.dry_run:
            try:
                from wxbot import quotes
                snap = quotes.snapshot(http, now)
            except Exception as exc:  # dashboard prices must never break trading
                logging.exception("quotes failed: %s", exc)
                snap = {}
            try:
                from wxbot import equity
                equity.record(now, (snap or {}).get("quotes") or {})
            except Exception as exc:  # dashboard history must never break trading
                logging.exception("equity failed: %s", exc)
        # reports last: balance, fills, settlements and live marks are all from this run
        try:
            if digest.anything_due(cfg, now) and cfg.get("account", "auto_bankroll", True):
                from wxbot import balance
                balance.refresh(http, now)   # balance again, after this run's orders
            for path in digest.run_due(cfg, now, http, dry_run=args.dry_run):
                print(f"report written: {path}")
            digest.run_money_summary(cfg, now, http, dry_run=args.dry_run)
        except Exception as exc:  # reports must never break trading
            logging.exception("reports failed: %s", exc)
        try:                         # milestone performance reviews (Alex, Nov 9)
            from wxbot import review
            for path in review.run(cfg, now, http, dry_run=args.dry_run):
                print(f"review written: {path}")
        except Exception as exc:  # reviews must never break trading
            logging.exception("reviews failed: %s", exc)
        print(f"trade ({out['mode']}): placed {len(out['placed'])}, skipped {len(out['skipped'])}"
              + (f", stops: {out['stops']}" if out["stops"] else ""))
        return 0

    if args.command == "tax":           # read-only: Kalshi fills + settlements -> reports/tax/
        import os
        from pathlib import Path
        from wxbot import taxledger
        from wxbot.kalshi_api import KalshiClient
        client = KalshiClient.from_env(Http(cfg.user_agent))
        if client is None:
            print("tax: needs KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY")
            return 1
        out = Path(os.environ["TAX_OUT"]) if os.environ.get("TAX_OUT") else taxledger.OUT
        print(f"tax ledger written: {taxledger.run(client, utcnow(), out)}")
        return 0

    if args.command == "checkpoint":
        from wxbot import checkpoint
        print(checkpoint.run(cfg, parse_iso(args.now) if args.now else utcnow()), end="")
        return 0

    if args.command == "backtest-fetch":
        from pathlib import Path
        from wxbot import backtest_fetch
        for line in backtest_fetch.run(Path(args.out), args.days, cfg.user_agent, recent_only=args.recent_only):
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
