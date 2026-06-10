import json, os, sys, urllib.request, urllib.parse

entries_path = sys.argv[1]
K = os.environ["TRELLO_KEY"]; T = os.environ["TRELLO_TOKEN"]

def card_first_label(cid):
    url = "https://api.trello.com/1/cards/%s?fields=labels&key=%s&token=%s" % (
        urllib.parse.quote(cid), K, T)
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            c = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return ("__404__", [])
        raise
    labs = c.get("labels", [])
    names = [l.get("name") or "(unnamed)" for l in labs]
    first = names[0] if names else "Uncategorized"
    return (first, names)

# read distinct (CardId, WorkType, CardTitle, hours, n)
rows = []
for line in open(entries_path, encoding="utf-8"):
    line = line.rstrip("\n")
    if not line:
        continue
    p = line.split("|")
    if len(p) < 5:
        continue
    rows.append((p[0], p[1], p[2], p[3], p[4]))

cache = {}
mism = []
checked = 0
for cid, wt, title, hrs, n in rows:
    if cid.startswith("kanban:"):
        continue
    checked += 1
    if cid not in cache:
        cache[cid] = card_first_label(cid)
    first, names = cache[cid]
    if first == "__404__":
        mism.append((float(hrs), title, wt, "CARD DELETED/NOT FOUND", ""))
    elif first != wt:
        extra = ("  [labels: " + ", ".join(names) + "]") if len(names) > 1 else ""
        mism.append((float(hrs), title, wt, first, extra))

print("Distinct card/worktype rows checked:", checked)
print()
if not mism:
    print("CLEAN: every May entry matches its card's current first label.")
else:
    print("DRIFT — stored WorkType (DB) != current Trello first-label:")
    print("  %-5s  %-42s  %-22s  %-22s" % ("Hrs", "Card", "Stored (DB)", "Current label (Trello)"))
    print("  " + "-" * 96)
    for hrs, title, wt, cur, extra in sorted(mism, reverse=True):
        print("  %-5s  %-42s  %-22s  %-22s%s" % (hrs, title[:42], wt, cur, extra))
    print("\nTotal drifted hours:", round(sum(m[0] for m in mism), 2))
