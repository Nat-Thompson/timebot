"""Post a comment (e.g. a Tempo time entry) and verify it.

Usage: python post_comment.py <card_shortlink> <body_file>
Tempo convention: the FIRST LINE must be `[date: YYYY-MM-DD] <summary> - <N> minutes`
(exact clock minutes, no other duration tokens on line 1, no em dashes).
"""
import sys
from _trello import call, get, assert_clean

card, body_file = sys.argv[1], sys.argv[2]
text = open(body_file, encoding="utf-8").read().strip()
action = call("POST", f"cards/{card}/actions/comments", {"text": text})
stored = get(f"actions/{action['id']}?fields=data")["data"]["text"]
assert_clean(stored, text)
print("OK", card, action["id"], "|", stored.splitlines()[0])
