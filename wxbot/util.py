"""Shared helpers: HTTP with retries/throttling, time parsing, JSON files, config."""
from __future__ import annotations

import json
import logging
import os
import re
import time
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

log = logging.getLogger("wxbot")

ROOT = Path(__file__).resolve().parent.parent
# small files committed to git (ledger, state)
DATA_DIR = Path(os.environ.get("WXBOT_DATA_DIR") or ROOT / "data")
# big/volatile files (forecasts, last scan), kept in the CI cache, not committed
CACHE_DIR = Path(os.environ.get("WXBOT_CACHE_DIR") or ROOT / "cache")

UTC = timezone.utc


class HttpError(Exception):
    def __init__(self, status: int, url: str, body: str = ""):
        super().__init__(f"HTTP {status} for {url}: {body[:200]}")
        self.status = status
        self.url = url
        self.body = body


class Http:
    """requests.Session wrapper: retries on 429/5xx, per-host throttling."""

    def __init__(self, user_agent: str, timeout: float = 20, retries: int = 3,
                 min_interval: float = 0.12):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self.timeout = timeout
        self.retries = retries
        self.min_interval = min_interval
        self._last: dict[str, float] = {}

    def _throttle(self, url: str) -> None:
        host = urlparse(url).netloc
        wait = self.min_interval - (time.monotonic() - self._last.get(host, 0.0))
        if wait > 0:
            time.sleep(wait)
        self._last[host] = time.monotonic()

    def _request(self, method: str, url: str, **kw):
        last_exc: Exception | None = None
        for attempt in range(self.retries):
            self._throttle(url)
            try:
                r = self.session.request(method, url, timeout=self.timeout, **kw)
            except requests.RequestException as exc:  # network hiccup
                last_exc = exc
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code == 429 or r.status_code >= 500:
                last_exc = HttpError(r.status_code, url, r.text)
                retry_after = r.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else 2.0 * (attempt + 1)
                time.sleep(min(delay, 20))
                continue
            if r.status_code >= 400:
                raise HttpError(r.status_code, url, r.text)
            return r
        if isinstance(last_exc, HttpError):
            raise last_exc
        raise HttpError(0, url, str(last_exc))

    def get_json(self, url: str, params: dict | None = None, headers: dict | None = None):
        return self._request("GET", url, params=params, headers=headers).json()

    def post_json(self, url: str, payload: dict, headers: dict | None = None):
        r = self._request("POST", url, json=payload, headers=headers)
        try:
            return r.json()
        except ValueError:
            return {}


# ---------------------------------------------------------------- time

def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    s = s.strip().replace("Z", "+00:00")
    # Trim fractional seconds beyond 6 digits (Kalshi sometimes sends 9)
    s = re.sub(r"(\.\d{6})\d+", r"\1", s)
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


_DUR = re.compile(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?$")


def parse_iso_duration(s: str) -> timedelta:
    m = _DUR.match(s)
    if not m:
        return timedelta(0)
    d, h, mi = (int(x) if x else 0 for x in m.groups())
    return timedelta(days=d, hours=h, minutes=mi)


def utcnow() -> datetime:
    return datetime.now(UTC)


def c_to_f(c: float) -> float:
    return c * 9.0 / 5.0 + 32.0


# ---------------------------------------------------------------- files

def read_json(path: Path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, sort_keys=True, default=str)
    os.replace(tmp, path)


# ---------------------------------------------------------------- config

@dataclass
class Config:
    raw: dict = field(default_factory=dict)

    def get(self, section: str, key: str, default=None):
        return self.raw.get(section, {}).get(key, default)

    # Convenience accessors used everywhere
    @property
    def bankroll(self) -> float:
        env = os.environ.get("BANKROLL")
        return float(env) if env else float(self.get("account", "bankroll", 35.0))

    @property
    def risk_level(self) -> str:
        env = (os.environ.get("RISK_LEVEL") or "").strip().lower()
        return env or str(self.get("account", "risk_level", "normal")).lower()

    @property
    def strategy_params(self) -> dict:
        params = dict(self.raw.get("strategy", {}))
        if self.risk_level == "aggressive":
            params.update(self.raw.get("strategy_aggressive", {}))
        return params

    @property
    def tz(self) -> str:
        return self.get("account", "timezone", "America/New_York")

    @property
    def user_agent(self) -> str:
        contact = os.environ.get("NWS_CONTACT") or self.get("account", "nws_contact", "")
        ua = "kalshi-weather-alerts/1.0"
        return f"{ua} ({contact})" if contact else ua


def load_config(path: Path | None = None) -> Config:
    path = path or (ROOT / "config.toml")
    with open(path, "rb") as f:
        return Config(tomllib.load(f))
