"""Mirror the Dev Ops board (5cIqQD15) onto the Savvy Otter Items board.

Usage:
  python devops_sync.py --baseline   # mark every current Dev Ops card seen + record positions (no Trello writes)
  python devops_sync.py --dry-run    # show what would change
  python devops_sync.py              # apply

Rules (set by Nat, 2026-10-01):
  1. A NEW Dev Ops card gets an Items card "[CLIENT] <title>" in Prioritized, label Operations
     (Nat's deploy hours are Operations), desc "Deployment request - <dev ops url>".
  2. When a Dev Ops card changes list, its linked Items card (desc contains the Dev Ops shortlink)
     moves to Doing. Exceptions: moves INTO Dev Ops "Done" are cleanup and are ignored; an Items
     card already in Done is NOT reopened when the Dev Ops card reaches "Deployed to Prod".
  3. A moved Dev Ops card with no linked Items card prints "UNLINKED MOVE": match it by hand
     (Nat's hand-made Items cards often lack links), add the link to the Items desc, then move it.
     Never auto-create those, because that duplicates his cards.
If state/ is missing (new machine or wiped), run --baseline first so old cards don't flood the board.
"""
import json
import os
import re
import sys
from _trello import DEVOPS, LABELS, LISTS, STATE, call, get, items_cards

SEEN = os.path.join(STATE, "devops_seen.json")
POSITIONS = os.path.join(STATE, "devops_lists.json")
DRY = "--dry-run" in sys.argv

CLIENTS = {  # Dev Ops prefix (lowercased, letters only) -> Items prefix
    "downtoearth": "DOWNTOEARTH", "dte": "DOWNTOEARTH", "flowerpower": "SCOTTBYRON",
    "sbcflowerpower": "SCOTTBYRON", "flowerpowernetproject": "SCOTTBYRON", "nextlevel": "NEXTLEVEL",
    "strata": "STRATA", "focalpointe": "FOCALPOINTE", "focalpointecashapp": "FOCALPOINTE",
    "focalpointearapp": "FOCALPOINTE", "fpyooz": "FOCALPOINTE", "perdido": "PERDIDO",
    "smartlink": "SMARTLINK", "winterberry": "WINTERBERRY", "diamond": "DIAMOND", "greenery": "GREENERY",
    "thegreenery": "GREENERY", "heartland": "HEARTLAND", "heartlandadp": "HEARTLAND",
    "ironclad": "IRONCLAD", "botanical": "BOTANICALDESIGNS", "botanicaldesigns": "BOTANICALDESIGNS",
    "atlasrfid": "ATLASRFID", "atlasre": "ATLASREALESTATE", "landscapeportal": "SAVVYOTTER",
    "neave": "NEAVE", "sitelandscape": "SITELANDSCAPE", "vgauge": "SCHOEL",
}


def items_name(devops_name):
    m = re.match(r"\s*\[([^\]]+)\]\s*-?\s*(.*)", devops_name)
    if not m:
        return None, devops_name.strip()
    client = CLIENTS.get(re.sub(r"[^a-z]", "", m.group(1).lower()))
    return client, f"[{client or m.group(1).strip().upper().replace(' ', '')}] {m.group(2).strip()}"


cards = get(f"boards/{DEVOPS}/cards?fields=name,shortLink,shortUrl,idList")
lists = {l["id"]: l["name"] for l in get(f"boards/{DEVOPS}/lists?fields=name")}
devops_done = next(i for i, n in lists.items() if n.strip().lower() == "done")
if "--baseline" in sys.argv:
    json.dump(sorted(c["shortLink"] for c in cards), open(SEEN, "w"))
    json.dump({c["shortLink"]: c["idList"] for c in cards}, open(POSITIONS, "w"))
    print("baseline:", len(cards), "Dev Ops cards marked seen, positions recorded")
    sys.exit()
if not (os.path.exists(SEEN) and os.path.exists(POSITIONS)):
    sys.exit("state missing: run --baseline first")

seen = set(json.load(open(SEEN)))
positions = json.load(open(POSITIONS))
items = items_cards()


def mirrors_of(sl):
    return [i for i in items if sl in (i.get("desc") or "")]


new = [c for c in cards if c["shortLink"] not in seen]
for c in new:
    if mirrors_of(c["shortLink"]):
        print("SKIP new (already mirrored)", c["shortLink"], c["name"])
    else:
        client, name = items_name(c["name"])
        flag = "" if client else "  [UNMAPPED CLIENT PREFIX: rename the card and add it to CLIENTS]"
        desc = (f"Deployment request - {c['shortUrl']}\n\nMirrored by WATCHER from the Dev Ops board "
                f"(list: {lists.get(c['idList'], '?')}). Label is Operations per Nat's deploy-hours rule.")
        if DRY:
            print("WOULD CREATE", c["shortLink"], "->", name, flag)
        else:
            made = call("POST", "cards", {"idList": LISTS["prioritized"], "idLabels": LABELS["operations"],
                                          "name": name, "desc": desc, "pos": "top"})
            assert made["name"] == name and made["desc"] == desc
            print("CREATED", made["shortLink"], "<-", c["shortLink"], "|", name, flag)
    seen.add(c["shortLink"])

moved = [c for c in cards if c["shortLink"] in positions and positions[c["shortLink"]] != c["idList"]]
for c in moved:
    frm, to = lists.get(positions[c["shortLink"]], "?"), lists.get(c["idList"], "?")
    if c["idList"] == devops_done:
        print("IGNORE cleanup move", c["shortLink"], frm, "->", to)
        continue
    ms = mirrors_of(c["shortLink"])
    if not ms:
        print("UNLINKED MOVE (match by hand)", c["shortLink"], c["name"], "|", frm, "->", to)
        continue
    for m in ms:
        if m["idList"] == LISTS["doing"]:
            print("ALREADY IN DOING", m["shortLink"], "<-", c["shortLink"], frm, "->", to)
        elif m["idList"] == LISTS["done"] and to.strip().lower() == "deployed to prod":
            print("KEEP DONE", m["shortLink"], "<-", c["shortLink"], frm, "->", to)
        elif DRY:
            print("WOULD MOVE TO DOING", m["shortLink"], m["name"], "| dev ops", frm, "->", to)
        else:
            r = call("PUT", f"cards/{m['shortLink']}", {"idList": LISTS["doing"], "pos": "top"})
            assert r["idList"] == LISTS["doing"]
            print("MOVED TO DOING", m["shortLink"], m["name"], "| dev ops", frm, "->", to)

if not DRY:
    json.dump(sorted(seen), open(SEEN, "w"))
    json.dump({c["shortLink"]: c["idList"] for c in cards}, open(POSITIONS, "w"))
print("new:", len(new), "moved:", len(moved))
