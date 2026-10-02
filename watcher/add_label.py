"""Add a label to a card and verify.

Usage: python add_label.py <card_shortlink> <label alias or id>
"""
import sys
from _trello import LABELS, call, get, resolve

card, label = sys.argv[1], resolve(sys.argv[2], LABELS)
call("POST", f"cards/{card}/idLabels", {"value": label})
c = get(f"cards/{card}?fields=name,labels")
assert label in [l["id"] for l in c["labels"]], "label not applied"
print("OK", card, [l["name"] for l in c["labels"]], "|", c["name"])
