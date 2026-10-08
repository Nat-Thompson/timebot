"""Build the WATCHER dashboard snapshot for Nat's live web page.

Usage: python dashboard.py            # writes state/dashboard_snapshot.json, prints a summary
       python dashboard.py --print    # also dumps the JSON

The page is a claude.ai Artifact whose shared database holds one document, dash/snapshot.
After running this, WATCHER writes the file into that document with ArtifactData `set`
(collection "dash", doc_id "snapshot", file_path state/dashboard_snapshot.json, if_version =
the version from the previous set; a refused set names the current version, retry with it).
See the items-watcher skill, step 9a.

Read-only on Trello. Sources: Items board lists, card comments (Tempo lines and status notes),
the Dev Ops board, and these state files: nat_actions.json, needs_reply.txt, scheduled_checks.txt,
thread_watches.txt, meetings_today.json.

Privacy: the snapshot holds card names, short status lines and links only. Status lines pass
through scrub(), which drops secret-looking values. Never add email bodies, passwords or tokens.
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from _trello import DEVOPS, LISTS, STATE, get

CT = ZoneInfo("America/Chicago")
NOW = datetime.now(timezone.utc)
TODAY = NOW.astimezone(CT).date().isoformat()
OUT = os.path.join(STATE, "dashboard_snapshot.json")

# Dev Ops pipeline, in order. Reviewing PR and CI/CD setup sit beside staging requests.
PIPELINE = ["Requested", "Deploy To Staging Request", "Deploy to Staging Approved", "Deploy to Staging",
            "Deployed to Staging", "Deploy to Prod Request", "Prod Approved", "Deploying to Prod",
            "Deployed to Prod", "Done"]
SIDE_STAGES = {"Reviewing PR": 1, "Setup for CI/CD Pipeline": 1}
STAGE_SHORT = {"Requested": "Requested", "Deploy To Staging Request": "Staging requested",
               "Deploy to Staging Approved": "Staging approved", "Deploy to Staging": "Deploying to staging",
               "Deployed to Staging": "On staging", "Deploy to Prod Request": "Prod requested",
               "Prod Approved": "Prod approved", "Deploying to Prod": "Deploying to prod",
               "Deployed to Prod": "On prod", "Done": "Done", "Reviewing PR": "Reviewing PR",
               "Setup for CI/CD Pipeline": "CI/CD setup"}

TEMPO = re.compile(r"^\[date:\s*(\d{4}-\d{2}-\d{2})\].*?-\s*(\d+(?:\.\d+)?)\s*(minutes?|mins?|hours?)\s*$", re.I)
WAITS = ["Reid", "515", "Mandeep", "Karan", "Navroze", "Amarinder", "Derrick", "Brad", "Kem", "Bill",
         "Paul", "Melanie", "Pierce", "Brian", "Les", "Focal Pointe", "client"]


def scrub(text):
    """Keep a status line safe for the page: no secret values, no long opaque strings."""
    text = re.sub(r"(?i)\b(password|passwd|pw|token|secret|api[_ -]?key|access[_ -]?key|passcode)\b\s*[:=]\s*\S+",
                  r"\1: [redacted]", text)
    text = re.sub(r"\bAKIA[0-9A-Z]{12,}\b", "[redacted]", text)
    text = re.sub(r"https?://\S*[?&](pwd|token|key|signature|sig)=\S+", "[link removed]", text)
    text = re.sub(r"(?<![\w/.-])(?=[A-Za-z0-9+/_-]*\d)(?=[A-Za-z0-9+/_-]*[A-Za-z])[A-Za-z0-9+/_-]{32,}={0,2}", "[redacted]", text)
    return text


def tidy(text):
    """Drop session ids and email addresses; the page shows names only."""
    text = re.sub(r"\s*\(?\b[\w.+-]+@[\w-]+\.[\w.]+\)?", "", text)
    return re.sub(r"\s*local_[0-9a-f-]{8,}", "", text)


def status_line(text, limit=240):
    lines = [l.strip(" -*|") for l in text.splitlines() if l.strip(" -*|")]
    if not lines:
        return ""
    line = re.sub(r"^\[(otter-ops|WATCHER|LANDSCAPE)\]\s*", "", lines[0])
    if len(line) < 60 and len(lines) > 1:
        line = f"{line} {lines[1]}"
    line = tidy(scrub(line.replace("—", ", ").replace("–", "-")))
    return line if len(line) <= limit else line[:limit - 1].rsplit(" ", 1)[0] + "..."


def age_hours(iso):
    return round((NOW - datetime.fromisoformat(iso.replace("Z", "+00:00"))).total_seconds() / 3600, 1)


def minutes(qty, unit):
    return round(float(qty) * (60 if unit.lower().startswith("h") else 1))


def client_of(name):
    m = re.match(r"\s*\[([^\]]+)\]\s*(.*)", name)
    return (m.group(1).strip(), m.group(2).strip(" -")) if m else ("", name.strip())


def devops_board():
    lists = {l["id"]: l["name"].strip() for l in get(f"boards/{DEVOPS}/lists?fields=name")}
    cards = get(f"boards/{DEVOPS}/cards?fields=name,shortLink,idList,dateLastActivity")
    return {c["shortLink"]: {**c, "stage": lists.get(c["idList"], "?")} for c in cards}


def stage_info(stage):
    idx = PIPELINE.index(stage) if stage in PIPELINE else SIDE_STAGES.get(stage, 0)
    # Progress runs to Deployed to Prod; Done counts as complete too.
    return {"stage": stage, "label": STAGE_SHORT.get(stage, stage), "step": min(idx, 8), "steps": 8}


def card_detail(c, devops):
    acts = get(f"cards/{c['id']}/actions?filter=commentCard&limit=1000&fields=date,data")
    today = total = 0
    last = logged = None
    for a in acts:  # newest first
        text = a["data"]["text"]
        m = TEMPO.match(text.splitlines()[0].strip()) if text.strip() else None
        if m:
            n = minutes(m.group(2), m.group(3))
            total += n
            today += n if m.group(1) == TODAY else 0
            if logged is None:
                summary = re.sub(r"^\[date:[^\]]*\]\s*|\s*-\s*\d+(\.\d+)?\s*\w+\s*$", "", text.splitlines()[0])
                logged = {"text": scrub(summary), "age_h": age_hours(a["date"]), "kind": "time entry"}
        elif last is None and status_line(text):
            last = {"text": status_line(text), "age_h": age_hours(a["date"])}
    client, title = client_of(c["name"])
    link = re.search(r"trello\.com/c/([A-Za-z0-9]{8})", c.get("desc") or "")
    d = {"short": c["shortLink"], "client": client, "title": title,
         "age_h": age_hours(c["dateLastActivity"]), "minutes_today": today, "minutes_total": total,
         "status": last or logged}
    if link and link.group(1) in devops:
        dv = devops[link.group(1)]
        d["devops"] = {"short": dv["shortLink"], **stage_info(dv["stage"])}
    return d


def waits_on(text):
    hits = [w for w in WAITS if re.search(rf"\b{re.escape(w)}\b", text or "")]
    return hits[:2]


def parse_pipes(fname, ncols):
    path = os.path.join(STATE, fname)
    if not os.path.exists(path):
        return []
    rows = []
    for line in open(path, encoding="utf-8"):
        if line.strip() and not line.lstrip().startswith("#"):
            parts = [p.strip() for p in line.split("|")]
            rows.append(parts + [""] * (ncols - len(parts)))
    return rows


def ct_age_h(stamp):
    """Hours since a 'YYYY-MM-DD HH:MM' Central (or ISO) stamp; None if unparseable."""
    for fmt, n in (("%Y-%m-%dT%H:%M:%SZ", 20), ("%Y-%m-%d %H:%M", 16), ("%Y-%m-%d", 10)):
        try:
            dt = datetime.strptime(stamp.strip()[:n], fmt)
            dt = dt.replace(tzinfo=timezone.utc if fmt.endswith("Z") else CT)
            return round((NOW - dt).total_seconds() / 3600, 1)
        except ValueError:
            continue
    return None


def build():
    devops = devops_board()
    lists = {name: get(f"lists/{lid}/cards?fields=name,desc,shortLink,dateLastActivity")
             for name, lid in LISTS.items()}
    snap = {"generated_at": NOW.isoformat(timespec="seconds"),
            "generated_ct": NOW.astimezone(CT).strftime("%a %b %d, %I:%M %p CT").replace(" 0", " "),
            "today": TODAY}
    snap["doing"] = [card_detail(c, devops) for c in lists["doing"]]
    # state/blocked_waits.json (kept by WATCHER) says who each Blocked card waits on. Cards without
    # an entry fall back to names found in the latest note, flagged as a guess on the page.
    bw_path = os.path.join(STATE, "blocked_waits.json")
    waits = json.load(open(bw_path, encoding="utf-8")) if os.path.exists(bw_path) else {}
    snap["blocked"] = []
    for c in lists["blocked"]:
        d = card_detail(c, devops)
        w = waits.get(c["shortLink"])
        if w and w.get("waiting_on"):
            d["waits_on"] = [w["waiting_on"]]
            d["blocker"] = status_line(w.get("blocker", ""), 240)
            d["waits_age_h"] = ct_age_h(w.get("since", ""))
            d["waits_guess"] = False
        else:
            d["waits_on"] = waits_on((d["status"] or {}).get("text", "") + " " + (c.get("desc") or "")[:600])
            d["waits_guess"] = True
        snap["blocked"].append(d)
    blocked_now = {c["shortLink"] for c in lists["blocked"]}
    snap["_waits_unset"] = sorted(blocked_now - set(waits))
    snap["_waits_stale"] = sorted(set(waits) - blocked_now)
    snap["with_client"] = [card_detail(c, devops) for c in lists["withclient"]]
    week_ago = (NOW - timedelta(days=3)).isoformat()
    recent_done = sorted([c for c in lists["done"] if c["dateLastActivity"] >= week_ago],
                         key=lambda c: c["dateLastActivity"], reverse=True)[:8]
    snap["done_recent"] = [{"short": c["shortLink"], **dict(zip(("client", "title"), client_of(c["name"]))),
                            "age_h": age_hours(c["dateLastActivity"])} for c in recent_done]
    snap["prioritized"] = {"count": len(lists["prioritized"]),
                           "top": [{"short": c["shortLink"], **dict(zip(("client", "title"), client_of(c["name"])))}
                                   for c in lists["prioritized"][:8]]}
    linked = {}
    for name, cards in lists.items():
        for c in cards:
            m = re.search(r"trello\.com/c/([A-Za-z0-9]{8})", c.get("desc") or "")
            if m:
                linked[m.group(1)] = c["shortLink"]
    snap["deployments"] = sorted(
        [{"short": s, **dict(zip(("client", "title"), client_of(dv["name"]))), **stage_info(dv["stage"]),
          "age_h": age_hours(dv["dateLastActivity"]), "items_card": linked.get(s)}
         for s, dv in devops.items() if dv["stage"] not in ("Deployed to Prod", "Done")],
        key=lambda d: (-d["step"], d["age_h"]))

    actions = json.load(open(os.path.join(STATE, "nat_actions.json"), encoding="utf-8")) \
        if os.path.exists(os.path.join(STATE, "nat_actions.json")) else []
    open_actions = [a for a in actions if not a.get("done")]
    for a in open_actions:
        a["age_h"] = ct_age_h(a.get("since", ""))
    snap["actions"] = sorted(open_actions, key=lambda a: (a.get("priority", 3), a.get("since", "")))
    snap["actions_done"] = sorted([a for a in actions if a.get("done") and (ct_age_h(a["done"]) or 0) <= 48],
                                  key=lambda a: a["done"], reverse=True)

    snap["needs_reply"] = [{"from": r[1], "subject": r[2], "since": r[3], "note": r[4],
                            "age_h": ct_age_h(r[3])} for r in parse_pipes("needs_reply.txt", 5)]
    snap["scheduled"] = [{"when": r[0], "title": status_line(r[1], 140)} for r in parse_pipes("scheduled_checks.txt", 3)]
    snap["watches"] = []
    for r in parse_pipes("thread_watches.txt", 6):
        session = re.sub(r"\s*local_[0-9a-f-]+", "", r[2]).strip()
        card = next((p for p in r[3:] if re.fullmatch(r"[A-Za-z0-9]{8}", p)), None)
        snap["watches"].append({"watch": status_line(r[1], 180), "session": session, "card": card})
    mt = os.path.join(STATE, "meetings_today.json")
    meet = json.load(open(mt, encoding="utf-8")) if os.path.exists(mt) else {}
    snap["meetings"] = meet.get("meetings", []) if meet.get("date") == TODAY else []
    snap["meetings_known"] = meet.get("date") == TODAY
    return snap


if __name__ == "__main__":
    snap = build()
    unset, stale = snap.pop("_waits_unset"), snap.pop("_waits_stale")
    # Card titles sometimes carry em dashes; the page never shows one (Nat's writing rule).
    body = json.dumps(snap, ensure_ascii=False, indent=1).replace(" — ", " - ").replace("—", " - ")
    assert len(body.encode("utf-8")) < 200_000, "snapshot too big for one db document"
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(body)
    if "--print" in sys.argv:
        print(body)
    print(f"OK {OUT} | {len(body)} bytes | {snap['generated_ct']} | actions {len(snap['actions'])} | "
          f"doing {len(snap['doing'])} | blocked {len(snap['blocked'])} | with client {len(snap['with_client'])} | "
          f"deploys {len(snap['deployments'])} | needs reply {len(snap['needs_reply'])}")
    if unset:
        print("BLOCKED, NO waiting_on in state/blocked_waits.json:", " ".join(unset))
    if stale:
        print("blocked_waits.json entries for cards no longer Blocked (remove them):", " ".join(stale))
