"""Shared helpers for the late-game / props research (offline analysis only)."""
from __future__ import annotations

import csv
import gzip
import math
from datetime import datetime, timezone
from pathlib import Path

OOS_TS = int(datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp())
LOT = 5  # contracts per order at a ~$50 bankroll; fees round up per order


def taker_fee(p, lot=LOT):
    return math.ceil(round(0.07 * lot * p * (1 - p) * 100, 6)) / 100 / lot


def maker_fee(p, lot=LOT):
    return math.ceil(round(0.0175 * lot * p * (1 - p) * 100, 6)) / 100 / lot


def rows(path):
    with gzip.open(path, "rt", newline="") as fh:
        yield from csv.DictReader(fh)


def runs(root, kind):
    """All run dirs for a role kind ('late' shards 0-1, 'props' shards 2-5)."""
    want = {"late": ("s0", "s1"), "props": ("s2", "s3", "s4", "s5")}[kind]
    return sorted(p for p in Path(root).glob("runs/*") if p.name.split("-")[-1] in want)


def stat(xs, clusters=None):
    """mean, 95% CI half-width (clustered if cluster ids given), n, n_clusters (cents)."""
    n = len(xs)
    if n == 0:
        return None
    m = sum(xs) / n
    if clusters is None:
        clusters = list(range(n))
    agg = {}
    for x, c in zip(xs, clusters):
        agg[c] = agg.get(c, 0.0) + (x - m)
    g = len(agg)
    var = sum(v * v for v in agg.values()) / (n * n) * (g / max(g - 1, 1))
    return m * 100, 1.96 * math.sqrt(var) * 100, n, g


def fmt(s):
    if not s:
        return "—"
    m, ci, n, g = s
    return f"{m:+.1f} ±{ci:.1f} (n{n}, {g}g)"
