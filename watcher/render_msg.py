"""Print the exact text of a background WATCHER message to another session (bg/msg_templates.py).

Usage: python render_msg.py <STAGING|PR-OWNER|CARD|REPLY|CHECK|DIGEST> <ref> [owner key from recipients.json]
       python render_msg.py recipients
Send the printed text with SendMessage unchanged; the guard hook denies anything else.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "bg"))
from _trello import STATE  # noqa: E402
from msg_templates import recipients, render  # noqa: E402

if len(sys.argv) == 2 and sys.argv[1] == "recipients":
    print("\n".join(sorted(recipients(STATE))))
    sys.exit()
if len(sys.argv) < 3:
    sys.exit(__doc__)
try:
    print(render(STATE, sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None))
except ValueError as e:
    sys.exit(f"REFUSED: {e}")
