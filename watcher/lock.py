"""One lock shared by the background runner and the interactive WATCHER, so a pass never runs twice.

Usage: python lock.py acquire <holder>    # holder: bg or interactive; exit 0 = got it, 1 = held
       python lock.py heartbeat <holder>  # refresh while a long pass runs
       python lock.py release <holder>
       python lock.py status
The lock file is state/watcher.lock: {holder, pid, started, heartbeat} (UTC ISO). A lock whose
heartbeat is older than STALE_MIN is taken over (a crashed pass must not block WATCHER forever).
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

from _trello import STATE

LOCK = os.path.join(STATE, "watcher.lock")
STALE_MIN = 20


def _now():
    return datetime.now(timezone.utc)


def read():
    try:
        return json.load(open(LOCK, encoding="utf-8"))
    except (OSError, ValueError):
        return None


def stale(info):
    try:
        return _now() - datetime.fromisoformat(info["heartbeat"]) > timedelta(minutes=STALE_MIN)
    except (KeyError, TypeError, ValueError):
        return True


def acquire(holder):
    info = {"holder": holder, "pid": os.getpid(), "started": _now().isoformat(timespec="seconds")}
    info["heartbeat"] = info["started"]
    for _ in range(2):
        try:
            fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            cur = read()
            if cur and not stale(cur):
                return False, cur
            try:
                os.remove(LOCK)  # stale (or unreadable): take it over
            except FileNotFoundError:
                pass
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(info, f)
        return True, info
    return False, read()


def heartbeat(holder):
    cur = read()
    if not cur or cur.get("holder") != holder:
        return False
    cur["heartbeat"] = _now().isoformat(timespec="seconds")
    with open(LOCK, "w", encoding="utf-8") as f:
        json.dump(cur, f)
    return True


def release(holder):
    cur = read()
    if cur and cur.get("holder") == holder:
        os.remove(LOCK)
        return True
    return False


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    who = sys.argv[2] if len(sys.argv) > 2 else ""
    if cmd == "acquire":
        ok, info = acquire(who)
        print("ACQUIRED" if ok else "HELD", json.dumps(info))
        sys.exit(0 if ok else 1)
    if cmd == "heartbeat":
        sys.exit(0 if heartbeat(who) else 1)
    if cmd == "release":
        print("RELEASED" if release(who) else "NOT HELD BY " + who)
        sys.exit(0)
    cur = read()
    print("FREE" if not cur else ("STALE " if stale(cur) else "HELD ") + json.dumps(cur))
