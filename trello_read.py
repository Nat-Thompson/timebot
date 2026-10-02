"""Read-only Trello API access for Claude Code sessions.

Only issues GET requests to api.trello.com. No writes, no deletes — by design,
so it can carry a narrow permission allow rule that bypasses the auto-mode
classifier. Credentials load from the .env next to this script; the token is
never printed.

Usage:
  python trello_read.py card <shortlink>          # name, desc, due, labels, list
  python trello_read.py comments <shortlink>      # all comments (text + date + author)
  python trello_read.py checklists <shortlink>    # checklists with item states
  python trello_read.py board-cards [board_id]    # open cards (default: TRELLO_BOARD_ID)
  python trello_read.py board-actions [board_id]  # recent board comment actions
  python trello_read.py raw <path?query>          # any GET, e.g. raw "cards/abc123?fields=name"
"""
import json
import os
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
env = {}
with open(ENV_PATH, encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")

KEY = env["TRELLO_API_KEY"]
TOKEN = env["TRELLO_API_TOKEN"]
BOARD = env.get("TRELLO_BOARD_ID", "")


def get(path_and_query: str):
    sep = "&" if "?" in path_and_query else "?"
    url = f"https://api.trello.com/1/{path_and_query}{sep}key={KEY}&token={TOKEN}"
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)


def out(data):
    text = json.dumps(data, indent=2, ensure_ascii=False)
    for secret in (KEY, TOKEN):  # belt and braces: never echo credentials
        text = text.replace(secret, "***")
    print(text)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]
    arg = sys.argv[2] if len(sys.argv) > 2 else None

    if cmd == "card":
        out(get(f"cards/{arg}?fields=name,desc,due,idList,labels,shortUrl,dateLastActivity"))
    elif cmd == "comments":
        acts = get(f"cards/{arg}/actions?filter=commentCard&limit=1000")
        out([
            {"id": a["id"], "date": a["date"],
             "author": a.get("memberCreator", {}).get("fullName"),
             "text": a["data"]["text"]}
            for a in acts
        ])
    elif cmd == "checklists":
        out(get(f"cards/{arg}/checklists?checkItem_fields=name,state"))
    elif cmd == "board-cards":
        out(get(f"boards/{arg or BOARD}/cards?fields=name,idList,labels,shortUrl"))
    elif cmd == "board-actions":
        out(get(f"boards/{arg or BOARD}/actions?filter=commentCard&limit=1000"))
    elif cmd == "raw":
        out(get(arg))
    else:
        print(f"unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
