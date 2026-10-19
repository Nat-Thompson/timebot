"""Shared Trello helpers for the WATCHER scripts.

Credentials come from timebot/.env (TRELLO_API_KEY, TRELLO_API_TOKEN). The token is never printed.
All writes send a JSON body via urllib (never curl, never query-string text), so UTF-8 and line
breaks survive; see ALEXANDRIA tempo.md for why.

Environment overrides (set by bg/runner.py):
  WATCHER_STATE    state directory (default: watcher/state)
  TIMEBOT_ENV      path to the .env file (default: timebot/.env)
  WATCHER_DRY_RUN  "1" = no Trello writes: call() prints WOULD and raises DryRun instead
"""
import json
import os
import re
import sys
import tempfile
import urllib.request

if sys.stdout:  # None under pythonw (Task Scheduler runs the background WATCHER that way)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.environ.get("WATCHER_STATE") or os.path.join(HERE, "state")
os.makedirs(STATE, exist_ok=True)
DRY = os.environ.get("WATCHER_DRY_RUN") == "1" or "--dry-run" in sys.argv


class DryRun(Exception):
    """Raised by call() in dry-run mode, after printing what it would have sent."""


def load_env():
    env = {}
    with open(os.environ.get("TIMEBOT_ENV") or os.path.join(HERE, "..", ".env"), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


_env = load_env()
AUTH = f"key={_env['TRELLO_API_KEY']}&token={_env['TRELLO_API_TOKEN']}"

# Savvy Otter Items board (mdS3ny24)
ITEMS = "mdS3ny24"
LISTS = {
    "prioritized": "68d191dfd3eac683543a2487",
    "doing": "67a23dd892ed3fb5e94c0fea",
    "blocked": "67a23e373d8815df1c20a6cd",
    "withclient": "67a3a892299d8b6c0478e413",
    "done": "67a23dd7893316d8822d07c7",
    # "With External Dev" (67ed1b09...) is ARCHIVED on purpose; waiting on 515 = blocked.
}
LABELS = {
    "project": "67a23dd748f259d85349dee0",
    "support-billable": "67a23dd748f259d85349dedc",
    "support-nonbillable": "67a23dd748f259d85349dedb",
    "operations": "67a23dd748f259d85349dede",
    "change-order": "67a23dd748f259d85349dedd",
    "retainer": "67a23dd748f259d85349dedf",
}
# Savvy Otter Dev Ops board
DEVOPS = "5cIqQD15"
DEVOPS_LISTS = {"devops-staging-request": "6a19aa90a44b0bb583441ff9"}  # Deploy To Staging Request


def _dry_hook(kind, value, tb):
    """In dry-run mode a write script ends at its first write: report it and exit cleanly."""
    if kind is DryRun:
        print("DRY RUN: nothing written")
        sys.exit(0)
    sys.__excepthook__(kind, value, tb)


sys.excepthook = _dry_hook


def resolve(value, table):
    """Accept an alias from `table` or a raw Trello id."""
    return table.get(value.lower(), value)


def get(path):
    sep = "&" if "?" in path else "?"
    with urllib.request.urlopen(f"https://api.trello.com/1/{path}{sep}{AUTH}", timeout=30) as r:
        return json.load(r)


def call(method, path, body=None):
    if DRY:
        print("WOULD", method, path.split("?")[0], json.dumps(body, ensure_ascii=False)[:300] if body else "")
        raise DryRun(f"{method} {path.split('?')[0]}")
    sep = "&" if "?" in path else "?"
    req = urllib.request.Request(
        f"https://api.trello.com/1/{path}{sep}{AUTH}",
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def assert_clean(text, expected):
    assert text == expected, "stored text differs from what was sent"
    assert not re.search(r"%[0-9A-Fa-f]{2}", text), "percent-encoding survived"


def save_json(path, data):
    """Atomic write: temp file in the same directory, then replace."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def save_text(path, text):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def items_cards():
    """All open Items cards, read list by list (the board-wide endpoint skips some)."""
    return [c for l in get(f"boards/{ITEMS}/lists?fields=id")
            for c in get(f"lists/{l['id']}/cards?fields=name,desc,shortLink,idList,dateLastActivity")]
