"""One WATCHER pass over the Items board Doing column.

Usage: python doing_diff.py [--since 2026-10-02T13:00:00Z]
Prints cards new to / gone from Doing since the last run, and board comments since --since
(default: the previous run's timestamp). Then refreshes the baseline. Nat has authorized
refreshing the baseline every pass.
Every NEW, GONE and non-Tempo COMMENT is also recorded in state/events.jsonl (journal.py), so the
change is not lost when the other WATCHER (background or interactive) ran the diff first.
"""
import datetime
import json
import os
import sys
from _trello import ITEMS, LISTS, STATE, get, save_json, save_text
from journal import add

BASE = os.path.join(STATE, "doing_baseline.json")
LAST = os.path.join(STATE, "last_pass.txt")
BY = os.environ.get("WATCHER_HOLDER", "interactive")

now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
since = sys.argv[sys.argv.index("--since") + 1] if "--since" in sys.argv else (
    open(LAST).read().strip() if os.path.exists(LAST) else now)

cards = get(f"lists/{LISTS['doing']}/cards?fields=name,shortLink,dateLastActivity")
old = {c["shortLink"] for c in json.load(open(BASE))} if os.path.exists(BASE) else {c["shortLink"] for c in cards}
cur = {c["shortLink"] for c in cards}
for c in cards:
    if c["shortLink"] not in old:
        print("NEW ", c["shortLink"], "|", c["name"])
        add("events", "doing_new", c["shortLink"], c["name"], by=BY)
for sl in sorted(old - cur):
    print("GONE", sl)
    add("events", "doing_gone", sl, "left Doing", by=BY)
print("DOING", len(cards), "cards:", ", ".join(f"{c['shortLink']} {c['name'][:30]}" for c in cards))

for a in get(f"boards/{ITEMS}/actions?filter=commentCard&since={since}&limit=50&fields=data,date"):
    first = a["data"]["text"].split("\n")[0]
    print("COMMENT", a["date"][5:16], a["data"]["card"]["shortLink"], "|", first[:110])
    if not first.lstrip().lower().startswith("[date:"):  # Tempo lines are not events
        add("events", "comment", a["id"], f"{a['data']['card']['shortLink']} | {first[:200]}", by=BY)

save_json(BASE, cards)
save_text(LAST, now)
print("pass at", now, "comments since", since)
