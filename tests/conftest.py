import os
import shutil
import tempfile

import pytest

# Point the bot's data/cache dirs at a temp folder BEFORE wxbot is imported.
_TMP = tempfile.mkdtemp(prefix="wxbot-test-")
os.environ["WXBOT_DATA_DIR"] = os.path.join(_TMP, "data")
os.environ["WXBOT_CACHE_DIR"] = os.path.join(_TMP, "cache")
os.environ.pop("NTFY_TOPIC", None)
os.environ.pop("BANKROLL", None)


@pytest.fixture(autouse=True)
def clean_dirs():
    for d in (os.environ["WXBOT_DATA_DIR"], os.environ["WXBOT_CACHE_DIR"]):
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d, exist_ok=True)
    yield


@pytest.fixture(autouse=True)
def no_aicost_network(monkeypatch):
    from wxbot import aicost
    monkeypatch.setattr(aicost, "_get", lambda path: None)   # reports never hit GitHub in tests
