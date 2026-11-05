"""Tiny read-only client for Kalshi's public market-data API (no key, no trading)."""
from __future__ import annotations

import threading
import time

import requests

K = "https://api.elections.kalshi.com/trade-api/v2"
S = requests.Session()
S.headers["User-Agent"] = "kalshi-research/0.1"
_lock = threading.Lock()
_last = [0.0]
T0 = time.time()
LOG: list[str] = []


def log(*a):
    msg = f"[{time.time() - T0:6.0f}s] " + " ".join(str(x) for x in a)
    print(msg, flush=True)
    LOG.append(msg)


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def get(path, params=None, quiet=False):
    for i in range(7):
        with _lock:
            wait = _last[0] + 0.06 - time.time()
            if wait > 0:
                time.sleep(wait)
            _last[0] = time.time()
        try:
            r = S.get(K + path, params=params, timeout=60)
        except requests.RequestException:
            time.sleep(2 * (i + 1))
            continue
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 * (i + 1))
            continue
        if not r.ok:
            if not quiet:
                log("HTTP", r.status_code, path, params, r.text[:200])
            return None
        return r.json()
    return None


def paginate(path, key, params, max_pages):
    out, cur = [], None
    for _ in range(max_pages):
        p = dict(params)
        if cur:
            p["cursor"] = cur
        js = get(path, p)
        if not js:
            break
        out += js.get(key) or []
        cur = js.get("cursor")
        if not cur:
            break
    return out
