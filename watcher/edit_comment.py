"""Edit an existing comment in place (keeps its original date and author) and verify it.

Usage: python edit_comment.py <card_shortlink> <action_id> <body_file>
"""
import sys
from _trello import call, get, assert_clean

card, action_id, body_file = sys.argv[1:4]
text = open(body_file, encoding="utf-8").read().strip()
call("PUT", f"cards/{card}/actions/{action_id}/comments", {"text": text})
a = get(f"actions/{action_id}?fields=data,date")
assert_clean(a["data"]["text"], text)
print("OK", card, action_id, a["date"][:10], "|", text.splitlines()[0])
