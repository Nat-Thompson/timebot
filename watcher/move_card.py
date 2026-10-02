"""Move a card to a list and verify.

Usage: python move_card.py <card_shortlink> <list>
<list> is an alias (prioritized, doing, blocked, withclient, done) or a raw list id
(raw ids work for the Dev Ops board too).
"""
import sys
from _trello import LISTS, call, resolve

card, target = sys.argv[1], resolve(sys.argv[2], LISTS)
c = call("PUT", f"cards/{card}", {"idList": target, "pos": "top"})
assert c["idList"] == target, "move did not stick"
print("OK", card, "->", sys.argv[2], "|", c["name"])
