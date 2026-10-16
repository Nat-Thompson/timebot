"""Append-only journals shared by the background runner and the interactive WATCHER.

  events  state/events.jsonl  every change a script detects (new Doing card, Dev Ops move, PR review
                              request...). Whoever handles one marks it, so the other side never redoes it.
  queue   state/queue.jsonl   work only the desktop app can do (spawn a chip, Teams check, filing or
                              board-move proposals, routing with no known owner).
  alerts  state/alerts.jsonl  P1 items the runner pushes to Nat (Windows toast + Slack BARK).

Usage: python journal.py <events|queue|alerts> open
       python journal.py <events|queue|alerts> add <kind> <ref> <detail...>
       python journal.py <events|queue|alerts> mark <id> <handled|queued|done|dropped> <by> [note...]
Lines are {id, ts, kind, ref, detail, status, by, note}. Status changes append a new line for the
same id (last line wins), so the file is never rewritten and two writers can't lose each other's lines.
Detail is short plain text: no email bodies, passwords or tokens.
"""
import json
import os
import sys
import uuid
from datetime import datetime, timezone

from _trello import STATE

FILES = {"events": "events.jsonl", "queue": "queue.jsonl", "alerts": "alerts.jsonl"}
OPEN = {"new"}


def path(name):
    return os.path.join(STATE, FILES[name])


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _append(name, rec):
    with open(path(name), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def entries(name):
    """Current state of every id (last line wins), in first-seen order."""
    cur = {}
    if os.path.exists(path(name)):
        for line in open(path(name), encoding="utf-8"):
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            cur[rec["id"]] = {**cur.get(rec["id"], {}), **rec}
    return list(cur.values())


def open_entries(name):
    return [e for e in entries(name) if e.get("status") in OPEN]


def add(name, kind, ref, detail, by="bg", dedupe=True):
    """Add an entry. With dedupe, an open entry with the same kind+ref is reused, not repeated."""
    if dedupe:
        for e in open_entries(name):
            if e.get("kind") == kind and e.get("ref") == ref:
                return e["id"]
    rec = {"id": uuid.uuid4().hex[:10], "ts": _now(), "kind": kind, "ref": ref,
           "detail": str(detail)[:400], "status": "new", "by": by}
    _append(name, rec)
    return rec["id"]


def mark(name, eid, status, by, note=""):
    if not any(e["id"] == eid for e in entries(name)):
        raise SystemExit(f"no such id {eid} in {name}")
    _append(name, {"id": eid, "status": status, "by": by, "marked": _now(), "note": str(note)[:300]})


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] not in FILES:
        sys.exit(__doc__)
    name, cmd = sys.argv[1], sys.argv[2]
    if cmd == "open":
        for e in open_entries(name):
            print(json.dumps(e, ensure_ascii=False))
    elif cmd == "add":
        print("ADDED", add(name, sys.argv[3], sys.argv[4], " ".join(sys.argv[5:]),
                           by=os.environ.get("WATCHER_HOLDER", "interactive")))
    elif cmd == "mark":
        mark(name, sys.argv[3], sys.argv[4], sys.argv[5], " ".join(sys.argv[6:]))
        print("MARKED", sys.argv[3], sys.argv[4])
    else:
        sys.exit(__doc__)
