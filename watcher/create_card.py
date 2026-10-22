"""Create a card from a JSON spec and verify.

Usage: python create_card.py <spec.json>
spec: {"list": "prioritized", "label": "operations", "name": "[CLIENT] title", "desc": "..."}
list and label take aliases from _trello.py or raw ids; list "doing" or "done" picks the half from
the name's prefix (_trello.side()). Never put passwords or secrets in desc.
"""
import json
import sys
from _trello import LABELS, LISTS, call, resolve, side

spec = json.load(open(sys.argv[1], encoding="utf-8"))
lst = spec["list"].lower()
lst = f"{lst}_{side(spec['name'])}" if lst in ("doing", "done") else spec["list"]
body = {"idList": resolve(lst, LISTS), "name": spec["name"], "desc": spec.get("desc", ""), "pos": "top"}
if spec.get("label"):
    body["idLabels"] = resolve(spec["label"], LABELS)
c = call("POST", "cards", body)
assert c["name"] == body["name"] and c.get("desc", "") == body["desc"], "stored text differs"
print("OK", c["shortLink"], [l["name"] for l in c["labels"]], "|", c["name"])
