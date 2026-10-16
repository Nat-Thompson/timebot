"""Shared "already processed" sets, so neither WATCHER handles the same email twice.

Usage: python seen.py email new <msg_id> [<msg_id> ...]   # prints the ids NOT yet seen
       python seen.py email add <msg_id> [<msg_id> ...]   # marks ids seen (append only)
Sets: email = state/email_seen_msgs.txt (one Gmail message id per line).
"""
import os
import re
import sys

from _trello import STATE

SETS = {"email": "email_seen_msgs.txt"}
ID = re.compile(r"^[0-9a-f]{16}$")

if len(sys.argv) < 4 or sys.argv[1] not in SETS or sys.argv[2] not in ("new", "add"):
    sys.exit(__doc__)
path = os.path.join(STATE, SETS[sys.argv[1]])
ids = [i for i in sys.argv[3:] if ID.match(i)]
bad = [i for i in sys.argv[3:] if not ID.match(i)]
if bad:
    print("IGNORED (not message ids):", " ".join(bad[:5]))
have = set(open(path, encoding="utf-8").read().split()) if os.path.exists(path) else set()
fresh = [i for i in dict.fromkeys(ids) if i not in have]
if sys.argv[2] == "new":
    print("NEW", " ".join(fresh) if fresh else "(none)")
else:
    with open(path, "a", encoding="utf-8") as f:
        f.writelines(i + "\n" for i in fresh)
    print("ADDED", len(fresh))
