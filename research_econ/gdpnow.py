"""Fetch the Atlanta Fed GDPNow forecast history workbook (read-only). Writes into argv[1]."""
import sys
from pathlib import Path

import requests

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
OUT.mkdir(parents=True, exist_ok=True)
U = "https://www.atlantafed.org/-/media/Project/Atlanta/FRBA/Documents/research-and-data/data/gdpnow/"
log = []
for name in ("GDPTrackingModelDataAndForecasts.xlsx", "GDPNowcastDataReleaseDates.xlsx"):
    r = requests.get(U + name, timeout=120, headers={"User-Agent": "Mozilla/5.0 (research)"})
    log.append(f"{name} {r.status_code} {len(r.content)}")
    if r.ok:
        (OUT / name).write_bytes(r.content)
(OUT / "log.txt").write_text("\n".join(log))
print("\n".join(log))
