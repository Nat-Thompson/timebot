"""Find existing cards before creating one (read-only). Matches every word, case-insensitive,
against card name + desc on the Items board (list by list, so no list is skipped) and the Dev Ops board.

Usage: python find_card.py <word> [<word> ...]     e.g. python find_card.py greenery payroll
       python find_card.py 1a13b22e5a3c28d0         (a Gmail thread id or a PR URL fragment also works)
"""
import sys

from _trello import DEVOPS, get, items_cards

words = [w.lower() for w in sys.argv[1:]]
if not words:
    sys.exit(__doc__)
devops = get(f"boards/{DEVOPS}/cards?fields=name,desc,shortLink,idList")
hits = 0
for board, cards in (("items", items_cards()), ("devops", devops)):
    for c in cards:
        text = f"{c['name']}\n{c.get('desc') or ''}".lower()
        if all(w in text for w in words):
            hits += 1
            print(board, c["shortLink"], "|", c["name"][:120])
print("matches:", hits)
