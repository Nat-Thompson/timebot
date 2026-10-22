"""Move a card to a list and verify.

Usage: python move_card.py <card_shortlink> <list>
<list> is an alias (prioritized, doing_client, doing_internal, blocked, withclient, done_client,
done_internal) or a raw list id (raw ids work for the Dev Ops board too). "doing" and "done" pick
the half from the card's prefix (_trello.side()).
"""
import sys
from _trello import LISTS, call, get, resolve, side

card, target = sys.argv[1], sys.argv[2].lower()
if target in ("doing", "done"):
    target = f"{target}_{side(get(f'cards/{card}?fields=name')['name'])}"
target = resolve(target, LISTS)
c = call("PUT", f"cards/{card}", {"idList": target, "pos": "top"})
assert c["idList"] == target, "move did not stick"
print("OK", card, "->", next((k for k, v in LISTS.items() if v == target), target), "|", c["name"])
