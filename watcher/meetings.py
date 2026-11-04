"""WATCHER step 7c: which meetings need a brief, which briefs exist, and what is late.

The script cannot reach Google Calendar itself (no Google credentials on this box), so WATCHER
calls the Calendar connector and saves the raw result, then runs this script on it:

  list_events  calendarId primary, startTime <today>T00:00:00, endTime <day after tomorrow>T00:00:00,
               timeZone America/Chicago, orderBy startTime, pageSize 100
  -> save the JSON response to state/calendar_raw.json

Usage (from the watcher dir):
  python meetings.py plan [state/calendar_raw.json] [--dry-run]
      Lists today's and tomorrow's meetings in CT, says which need a brief, which have one, and
      which are late. Writes state/calendar_seen.json and state/meetings_today.json, and adds a
      needs-Nat item to state/nat_actions.json for any meeting still without a brief 30 minutes
      before it starts. --dry-run prints the same and writes nothing.
  python meetings.py mark <event_id> <brief_path>
      Records a brief you wrote (after SendUserFile) so later passes stop asking for it.
  python meetings.py check
      Exit 2 and print ALARM if calendar_seen.json is missing or older than 1 day. doing_diff.py
      runs this check on every pass, so a dead step 7c can never be silent again.

Who gets a brief: a meeting with anyone outside savvyotter.com, anyone from 515, or a client or
project keyword (the ALEXANDRIA client folder names plus ALIASES) in the title or description.
Skipped: Nat's own events with no other attendee, PLACEHOLDER and deploy blocks Nat organizes,
declined or cancelled events, all-day events, and out-of-office / focus / working-location blocks.
"""
import datetime
import glob
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.environ.get("WATCHER_STATE") or os.path.join(HERE, "state")
SEEN = os.path.join(STATE, "calendar_seen.json")
TODAY_FILE = os.path.join(STATE, "meetings_today.json")
ACTIONS = os.path.join(STATE, "nat_actions.json")
BRIEF_DIR = r"C:\minions\neave"
CLIENTS_DIR = r"C:\minions\SavvyAI\ALEXANDRIA\clients"

BRIEF_LEAD_MIN = 60    # brief written at least this long before the start
NEEDS_NAT_MIN = 30     # still no brief this close to the start: needs-Nat item
STALE_HOURS = 24

ME = "nat.thompson@savvyotter.com"
# 515 people use savvyotter.com addresses, so they are named here.
P515 = ("navroze", "karanpreet", "mandeep", "515solutions")
ALIASES = ["next level", "nl", "fp", "focal", "mdm", "gbm", "atlas", "rfid", "cre", "ikon", "halstead",
           "vgauge", "dte", "heartland", "adp", "weathermatic", "aspire", "515"]
NOT_KEYWORDS = {"savvyotter", "neave"}  # client folders that are us or too generic to match on
SKIP_TITLE = re.compile(r"^\s*(placeholder\b|staging\s*:)|\bdeploy|\bprod\b.*\b(fix|phase|upgrade|release)|"
                        r"\bphase [a-z0-9]+ on prod|\bmaintenance\b|\bpatch(ing)?\b", re.I)

try:
    from zoneinfo import ZoneInfo
    CT = ZoneInfo("America/Chicago")
except Exception:  # no tzdata: this box runs on CT, so local time is CT
    CT = None


def now_ct():
    return datetime.datetime.now(CT) if CT else datetime.datetime.now().astimezone()


def to_ct(s):
    d = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d.astimezone(CT) if CT else d.astimezone()


def hm(d):
    return d.strftime("%I:%M %p").lstrip("0")


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)


def keywords():
    words = set(ALIASES)
    if os.path.isdir(CLIENTS_DIR):
        for d in os.listdir(CLIENTS_DIR):
            if d not in NOT_KEYWORDS:
                words.add(d.replace("-", " "))
    return sorted(words, key=len, reverse=True)


def name_of(a):
    return a.get("displayName") or a["email"].split("@")[0].replace(".", " ").title()


def slug(title):
    t = re.sub(r"\[[^\]]*\]", " ", title.lower())
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")[:50].rstrip("-") or "meeting"


def classify(ev, kws):
    """Return (brief?, reason). reason explains the decision either way."""
    if ev.get("status") == "cancelled":
        return False, "cancelled"
    if ev.get("eventType", "DEFAULT") not in ("DEFAULT", "FROM_GMAIL"):
        return False, ev.get("eventType", "").lower().replace("_", " ")
    if "dateTime" not in ev.get("start", {}):
        return False, "all-day"
    att = ev.get("attendees", [])
    me = next((a for a in att if a.get("self") or a.get("email") == ME), None)
    if me and me.get("responseStatus") == "declined":
        return False, "declined"
    mine = (ev.get("organizer") or {}).get("self") or (ev.get("organizer") or {}).get("email") == ME
    others = [a for a in att if not (a.get("self") or a.get("email") == ME or a.get("resource"))]
    title = ev.get("summary", "")
    if mine and (not others or SKIP_TITLE.search(title)):
        return False, "Nat's own block" if not others else "placeholder/deploy block"
    external = sorted({a["email"].split("@")[1] for a in others if not a["email"].endswith("@savvyotter.com")})
    if external:
        return True, "external: " + ", ".join(external)
    p515 = [name_of(a) for a in others if any(p in a["email"].lower() for p in P515)]
    if p515:
        return True, "515: " + ", ".join(p515)
    text = (title + " " + re.sub(r"<[^>]+>", " ", ev.get("description", ""))[:2000]).lower()
    hit = next((k for k in kws if re.search(r"(?<![a-z0-9])" + re.escape(k) + r"(?![a-z0-9])", text)), None)
    if hit:
        return True, "keyword: " + hit
    return False, "internal, no client keyword"


def existing_brief(ev_id, day, s, briefs):
    rec = briefs.get(ev_id)
    if rec and os.path.exists(rec["path"]):
        return rec["path"], True
    exact = os.path.join(BRIEF_DIR, f"{s}-brief-{day}.html")
    if os.path.exists(exact):
        return exact, True
    # An ad hoc brief for the same day that shares a word with the title: a hint, not proof.
    words = {w for w in s.split("-") if len(w) >= 3}
    for p in sorted(glob.glob(os.path.join(BRIEF_DIR, f"*brief*{day}*"))):
        if words & set(re.split(r"[^a-z0-9]+", os.path.basename(p).lower())):
            return p, False
    return None, False


def check(seen=None):
    seen = load(SEEN, None) if seen is None else seen
    stamp = (seen or {}).get("read_at")
    if not stamp:
        return f"ALARM calendar_seen.json has no read_at (last written {mtime_ct(SEEN)}): step 7c is not running"
    age = (now_ct() - to_ct(stamp)).total_seconds() / 3600
    if age > STALE_HOURS:
        return f"ALARM calendar last read {age:.0f} h ago ({hm(to_ct(stamp))} {to_ct(stamp):%m/%d} CT): step 7c is not running"
    return None


def mtime_ct(path):
    if not os.path.exists(path):
        return "never"
    d = datetime.datetime.fromtimestamp(os.path.getmtime(path)).astimezone()
    return f"{d:%m/%d} {hm(d)}"


def upsert_action(item, done=False):
    acts = load(ACTIONS, [])
    cur = next((a for a in acts if a.get("id") == item["id"]), None)
    stamp = now_ct().strftime("%Y-%m-%d %H:%M")
    if done:
        if cur and not cur.get("done"):
            cur["done"] = stamp
            save(ACTIONS, acts)
        return
    if cur is None:
        acts.append(item)
    elif cur.get("done"):
        cur.pop("done")
        cur.update(item)
    else:
        return
    save(ACTIONS, acts)


def plan(raw_path, dry):
    raw = load(raw_path, None)
    if raw is None:
        print(f"ERROR cannot read {raw_path}. Save the list_events response there first.")
        sys.exit(1)
    events = raw.get("events", raw) if isinstance(raw, dict) else raw
    now = now_ct()
    today, tomorrow = now.date(), now.date() + datetime.timedelta(days=1)
    seen = load(SEEN, {})
    old = seen.get("seen", {})
    briefs = seen.get("briefs", {})
    kws = keywords()
    rows, today_rows, late = [], [], []
    for ev in events:
        if "dateTime" not in ev.get("start", {}):
            continue
        start, end = to_ct(ev["start"]["dateTime"]), to_ct(ev["end"]["dateTime"])
        if start.date() not in (today, tomorrow):
            continue
        need, why = classify(ev, kws)
        day = start.strftime("%Y-%m-%d")
        s = slug(ev.get("summary", ""))
        path, sure = existing_brief(ev["id"], day, s, briefs)
        change = "NEW " if ev["id"] not in old else ("CHANGED " if old[ev["id"]] != ev.get("updated") else "")
        due = start - datetime.timedelta(minutes=BRIEF_LEAD_MIN)
        if not need:
            state = "skip"
        elif end <= now:
            state = "over"
        elif path and sure:
            state = "briefed"
        elif now >= start - datetime.timedelta(minutes=NEEDS_NAT_MIN):
            state = "NEEDS-NAT"
        elif now >= due:
            state = "LATE, write now"
        elif due - now <= datetime.timedelta(hours=18):
            state = "WRITE"
        else:
            state = "later"
        others = [name_of(a) for a in ev.get("attendees", []) if not (a.get("self") or a.get("email") == ME)]
        rows.append((start, f"{change}{state:16} {start:%a %m/%d} {hm(start)}-{hm(end)} CT | {ev.get('summary', '')} "
                            f"| {why} | with {', '.join(others) or 'nobody'}"
                            + (f" | brief due {hm(due)} -> {os.path.join(BRIEF_DIR, s + '-brief-' + day + '.html')}"
                               if state in ("WRITE", "LATE, write now", "later") else "")
                            + (f" | brief {path}" if path and sure else "")
                            + (f" | maybe already: {path} (mark it if so)" if path and not sure and need else "")
                            + f" | id {ev['id']}"))
        if state == "NEEDS-NAT":
            late.append((ev, start))
        if start.date() == today:
            me = next((a for a in ev.get("attendees", []) if a.get("self")), {})
            today_rows.append({"start": hm(start), "end": hm(end), "title": ev.get("summary", ""),
                               "attendees": others, "status": me.get("responseStatus", "accepted"),
                               "card": None, "brief": os.path.basename(path) if path and sure else
                               ("not needed" if not need else "missing")})
    for _, line in sorted(rows, key=lambda r: r[0]):
        print(line)
    if not rows:
        print("no timed events today or tomorrow")
    for ev, start in late:
        print(f"NEEDS-NAT no brief for '{ev.get('summary', '')}' at {hm(start)} CT: added to nat_actions.json"
              + (" (dry run: not added)" if dry else ""))
    if dry:
        print("dry run: state not written")
        return
    for ev, start in late:
        upsert_action({"id": f"brief-{ev['id']}", "priority": 1, "since": today.isoformat(),
                       "text": f"No brief for '{ev.get('summary', '')}' at {hm(start)} CT. WATCHER could not produce "
                               f"one in time: prep from the calendar invite, or tell WATCHER what to pull.",
                       "due": f"{start:%Y-%m-%d} {hm(start)}"})
    for eid in briefs:  # a brief that landed after the alarm closes the needs-Nat item
        upsert_action({"id": f"brief-{eid}"}, done=True)
    upsert_action({"id": "calendar-stale"}, done=True)
    seen.update({"read_at": now.isoformat(timespec="seconds"), "date": today.isoformat(),
                 "seen": {ev["id"]: ev.get("updated") for ev in events}, "briefs": briefs})
    save(SEEN, seen)
    save(TODAY_FILE, {"date": today.isoformat(), "meetings": today_rows})
    print(f"saved calendar_seen.json and meetings_today.json at {hm(now)} CT")


def mark(ev_id, path):
    path = os.path.abspath(path)
    if not os.path.exists(path):
        print(f"ERROR {path} does not exist")
        sys.exit(1)
    seen = load(SEEN, {})
    seen.setdefault("briefs", {})[ev_id] = {"path": path, "at": now_ct().isoformat(timespec="seconds")}
    save(SEEN, seen)
    # meetings_today.json picks the brief up on the next plan run (it reads calendar_seen briefs).
    upsert_action({"id": f"brief-{ev_id}"}, done=True)
    print(f"marked {ev_id} -> {path}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "plan"
    if cmd == "plan":
        args = [a for a in sys.argv[2:] if not a.startswith("--")]
        plan(args[0] if args else os.path.join(STATE, "calendar_raw.json"), "--dry-run" in sys.argv)
    elif cmd == "mark" and len(sys.argv) == 4:
        mark(sys.argv[2], sys.argv[3])
    elif cmd == "check":
        msg = check()
        print(msg or "calendar read within the last day")
        if msg:
            if "--no-action" not in sys.argv:
                upsert_action({"id": "calendar-stale", "priority": 1, "since": now_ct().date().isoformat(),
                               "text": "WATCHER has not read the calendar in over a day, so meeting briefs are not "
                                       "being written. Check that WATCHER's loop is running."})
            sys.exit(2)
    else:
        print(__doc__)
        sys.exit(1)
