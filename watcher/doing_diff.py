"""One WATCHER pass over the Items board Doing columns, Doing (Client) and Doing (Internal).

Usage: python doing_diff.py [--since 2026-10-02T13:00:00Z] [--dry-run]
Prints cards new to / gone from Doing since the last run, cards that switched halves, cards sitting
in the wrong half for their prefix, and board comments since --since (default: the previous run's
timestamp). Then refreshes the baseline (not with --dry-run). Nat has authorized refreshing the
baseline every pass.
"""
import datetime
import json
import os
import sys
from _trello import DOING, ITEMS, STATE, get, side

BASE = os.path.join(STATE, "doing_baseline.json")
LAST = os.path.join(STATE, "last_pass.txt")
DRY = "--dry-run" in sys.argv

now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
since = sys.argv[sys.argv.index("--since") + 1] if "--since" in sys.argv else (
    open(LAST).read().strip() if os.path.exists(LAST) else now)

cards = [{**c, "column": col} for lid, col in DOING.items()
         for c in get(f"lists/{lid}/cards?fields=name,shortLink,dateLastActivity")]
# Baselines from before the split have no "column"; treat those as client (the old Doing id).
old = {c["shortLink"]: c.get("column", "client") for c in json.load(open(BASE))} if os.path.exists(BASE) \
    else {c["shortLink"]: c["column"] for c in cards}
for c in cards:
    if c["shortLink"] not in old:
        print(f"NEW  [{c['column']}]", c["shortLink"], "|", c["name"])
    elif old[c["shortLink"]] != c["column"]:
        print(f"SWITCHED [{old[c['shortLink']]} -> {c['column']}]", c["shortLink"], "|", c["name"])
    if side(c["name"]) != c["column"]:
        print(f"WRONG HALF [in {c['column']}, rule says {side(c['name'])}]", c["shortLink"], "|", c["name"])
for sl in sorted(set(old) - {c["shortLink"] for c in cards}):
    print(f"GONE [{old[sl]}]", sl)
for col in ("client", "internal"):
    mine = [c for c in cards if c["column"] == col]
    print(f"DOING ({col})", len(mine), "cards:", ", ".join(f"{c['shortLink']} {c['name'][:30]}" for c in mine))

for a in get(f"boards/{ITEMS}/actions?filter=commentCard&since={since}&limit=50&fields=data,date"):
    print("COMMENT", a["date"][5:16], a["data"]["card"]["shortLink"], "|", a["data"]["text"].split("\n")[0][:110])

if DRY:
    print("dry run at", now, "comments since", since, "(baseline not refreshed)")
else:
    json.dump(cards, open(BASE, "w"))
    open(LAST, "w").write(now)
    print("pass at", now, "comments since", since)
