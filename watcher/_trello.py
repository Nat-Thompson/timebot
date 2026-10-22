"""Shared Trello helpers for the WATCHER scripts.

Credentials come from timebot/.env (TRELLO_API_KEY, TRELLO_API_TOKEN). The token is never printed.
All writes send a JSON body via urllib (never curl, never query-string text), so UTF-8 and line
breaks survive; see ALEXANDRIA tempo.md for why.
"""
import json
import os
import re
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
# WATCHER_STATE lets a worktree copy dry-run against the live state folder.
STATE = os.environ.get("WATCHER_STATE") or os.path.join(HERE, "state")
os.makedirs(STATE, exist_ok=True)

_env = {}
_dir = HERE  # timebot/.env, found by walking up so worktree copies work too
while not os.path.exists(os.path.join(_dir, ".env")) and os.path.dirname(_dir) != _dir:
    _dir = os.path.dirname(_dir)
with open(os.path.join(_dir, ".env"), encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            _env[k.strip()] = v.strip().strip('"').strip("'")
AUTH = f"key={_env['TRELLO_API_KEY']}&token={_env['TRELLO_API_TOKEN']}"

# Savvy Otter Items board (mdS3ny24)
ITEMS = "mdS3ny24"
# Doing and Done are split into Client and Internal (Reid's request, 2026-10-20). There is no plain
# "doing" or "done" id on purpose: pick the half with side() / doing_for() / done_for(). (move_card.py
# and create_card.py accept "doing" / "done" and resolve them that way.)
LISTS = {
    "prioritized": "68d191dfd3eac683543a2487",
    "doing_client": "67a23dd892ed3fb5e94c0fea",
    "doing_internal": "6ad7bc00f1dc5ce79e2ed466",
    "blocked": "67a23e373d8815df1c20a6cd",
    "withclient": "67a3a892299d8b6c0478e413",
    "done_client": "67a23dd7893316d8822d07c7",
    "done_internal": "6ad7bc1e7fe02ddf4d6eb551",
    # "With External Dev" (67ed1b09...) is ARCHIVED on purpose; waiting on 515 = blocked.
}
DOING = {LISTS["doing_client"]: "client", LISTS["doing_internal"]: "internal"}
DONE = {LISTS["done_client"]: "client", LISTS["done_internal"]: "internal"}
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


INTERNAL_PREFIXES = {"SAVVYOTTER", "SAVVYOTER"}
INTERNAL_TOOLS = re.compile(r"\s*(Landscape Portal|WATCHER|ALEXANDRIA|SHERLOCK|Tempo|win[ -]tracker)", re.I)


def prefix(name):
    """The [CLIENT] prefix of a card name, uppercased, or None."""
    m = re.match(r"\s*\[([^\]]+)\]", name)
    return m.group(1).strip().upper() if m else None


def side(name):
    """'internal' or 'client' (rule set by Nat, 2026-10-20). The prefix wins: [SAVVYOTTER] is internal,
    any other prefix is client, whatever the label (deploy mirrors are all Operations, so the label
    says nothing). An unprefixed card is internal only if its title starts with an internal tool name;
    otherwise it is client and should get a prefix."""
    p = prefix(name)
    if p:
        return "internal" if p in INTERNAL_PREFIXES else "client"
    return "internal" if INTERNAL_TOOLS.match(name) else "client"


def doing_for(name):
    return LISTS["doing_" + side(name)]


def done_for(name):
    return LISTS["done_" + side(name)]


def resolve(value, table):
    """Accept an alias from `table` or a raw Trello id."""
    return table.get(value.lower(), value)


def get(path):
    sep = "&" if "?" in path else "?"
    with urllib.request.urlopen(f"https://api.trello.com/1/{path}{sep}{AUTH}", timeout=30) as r:
        return json.load(r)


def call(method, path, body=None):
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


def items_cards():
    """All open Items cards, read list by list (the board-wide endpoint skips some)."""
    return [c for l in get(f"boards/{ITEMS}/lists?fields=id")
            for c in get(f"lists/{l['id']}/cards?fields=name,desc,shortLink,idList,dateLastActivity")]
