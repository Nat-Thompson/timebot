"""PreToolUse guard for the background WATCHER's headless Claude run. Deny by default.

Claude Code runs this before every tool call (bg/runner.py puts it in the run's --settings). It reads
the hook JSON on stdin; exit 0 allows the call, exit 2 blocks it (the reason on stderr goes back to
Claude). Every decision is appended to <state>/logs/guard-YYYY-MM-DD.log, tool name and verdict only.

It enforces the plan's read-and-route rules even if the permission rules were wrong or a prompt
injection talked the model into something: no email sends/replies/drafts, no deletes or trashes, no
calendar changes, no sharing, no Chrome, no web, no shell beyond the listed watcher scripts, file writes
only inside the state dir, Drive files only in the Meeting Briefs folder, the dashboard only, and
session messages only as exact templates (msg_templates.py). Dry run blocks every external write.

Environment (set by runner.py): WATCHER_DIR, WATCHER_STATE, WATCHER_DRY_RUN, ALEXANDRIA_DIR.
"""
import json
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from msg_templates import matches, recipients  # noqa: E402

WATCHER_DIR = os.environ.get("WATCHER_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.environ.get("WATCHER_STATE") or os.path.join(WATCHER_DIR, "state")
ALEXANDRIA = os.environ.get("ALEXANDRIA_DIR") or r"C:\minions\SavvyAI\ALEXANDRIA"
DRY = os.environ.get("WATCHER_DRY_RUN") == "1"

DASHBOARD = "https://claude.ai/artifact/Et7XXeZL4AC3MgLzGv47xq"
BRIEFS_FOLDER = "1QjarRnepb-Rdg1zsPD4eHXUqZh5b7lCA"
SKILL = "watcher-bg-pass"

GMAIL = "mcp__claude_ai_Gmail__"
CAL = "mcp__claude_ai_Google_Calendar__"
DRIVE = "mcp__claude_ai_Google_Drive__"
READ_ONLY_MCP = {GMAIL + t for t in ("search_threads", "get_thread", "get_message", "list_labels")} | \
                {CAL + t for t in ("list_events", "get_event", "list_calendars")} | \
                {DRIVE + t for t in ("read_file_content", "search_files", "get_file_metadata")}

# Watcher scripts Claude may run (cwd = watcher dir). Each one honors WATCHER_DRY_RUN itself too.
SCRIPTS = {"find_card", "create_card", "add_label", "post_comment", "journal", "dashboard",
           "render_msg", "seen"}
SAFE_ARG = re.compile(r"^[\w.:/\\=@#+,%-]+$")
# Files in state/ the background job must never write: the interactive WATCHER owns them, or they
# are written only through a script (journals) or by the runner (lock).
PROTECTED = {"recipients.json", "card_owners.json", "watcher.lock", "events.jsonl", "queue.jsonl",
             "alerts.jsonl", "usage.json"}


def norm(p):
    p = (p or "").replace("\\", "/")
    m = re.match(r"^/([a-zA-Z])/(.*)$", p)
    if m:
        p = f"{m.group(1)}:/{m.group(2)}"
    return os.path.normcase(os.path.normpath(p))


def under(path, root):
    path, root = norm(path), norm(root)
    return path == root or path.startswith(root.rstrip("\\/") + os.sep)


def deny(why):
    return False, why


def check(tool, inp):
    """(allowed, reason) for one tool call."""
    if tool == "ToolSearch" or tool == "ListAgents":
        return True, "read"
    if tool == "Skill":
        return (inp.get("skill") == SKILL, "only the watcher-bg-pass skill")
    if tool in ("Read", "Glob", "Grep"):
        p = inp.get("file_path") or inp.get("path") or ""
        if not p:
            return deny("a path is required")
        if any(under(p, r) for r in (WATCHER_DIR, STATE, ALEXANDRIA)) and not norm(p).endswith(".env"):
            return True, "read in allowed dirs"
        return deny("reads are limited to the watcher dir, its state and ALEXANDRIA")
    if tool in ("Write", "Edit"):
        p = inp.get("file_path", "")
        if not under(p, STATE):
            return deny("writes are limited to the state dir")
        if os.path.basename(norm(p)) in PROTECTED:
            return deny("that state file is owned by the interactive WATCHER or a script")
        return True, "state write"
    if tool == "Bash":
        cmd = (inp.get("command") or "").strip()
        parts = cmd.split()
        if len(parts) < 2 or parts[0] not in ("python", "python3") or "\n" in cmd:
            return deny("only `python <watcher script>.py args` is allowed")
        script = parts[1].replace("\\", "/")
        if script == "../trello_read.py":  # the repo-root GET-only reader, by exact relative name
            if len(parts) < 3 or parts[2] not in ("card", "comments", "checklists", "board-cards"):
                return deny("trello_read: only card, comments, checklists, board-cards")
            name = "trello_read"
        else:
            name = script[:-3] if script.endswith(".py") else ""
        if "/" in name or name not in SCRIPTS | {"trello_read"} or (name == "trello_read" and script != "../trello_read.py"):
            return deny(f"script not allowed: {parts[1]} (run from the watcher dir, no path)")
        bad = [a for a in parts[2:] if not SAFE_ARG.match(a)]
        if bad:
            return deny(f"unsafe characters in arguments: {bad[:3]}")
        if name == "post_comment" and DRY:
            return deny("dry run: no Trello comments")
        return True, "watcher script"
    if tool in READ_ONLY_MCP:
        return True, "connector read"
    if tool == DRIVE + "create_file":
        if DRY:
            return deny("dry run: no Drive files")
        if inp.get("parentId") != BRIEFS_FOLDER:
            return deny("Drive files may only go in the Meeting Briefs folder")
        if inp.get("contentMimeType") != "text/html" or inp.get("base64Content") or inp.get("content") \
                or inp.get("mimeType") or inp.get("disableConversionToGoogleType"):
            return deny("briefs are textContent HTML converted to a Google Doc")
        return True, "meeting brief"
    if tool == "ArtifactData":
        if inp.get("url") != DASHBOARD:
            return deny("only the WATCHER dashboard")
        act = inp.get("action")
        if act in ("get", "list"):
            return (inp.get("collection") == "dash", "dash collection only")
        if act == "set":
            if DRY:
                return deny("dry run: no dashboard writes")
            ok = (inp.get("collection") == "dash" and inp.get("doc_id") == "snapshot" and not inp.get("data")
                  and norm(inp.get("file_path")) == norm(os.path.join(STATE, "dashboard_snapshot.json")))
            return (ok, "set dash/snapshot from state/dashboard_snapshot.json only")
        return deny(f"ArtifactData action {act} not allowed")
    if tool == "SendMessage":
        if DRY:
            return deny("dry run: no session messages")
        if inp.get("notify_when_idle"):
            return deny("no idle subscriptions")
        if inp.get("to") not in recipients(STATE):
            return deny("recipient is not in recipients.json or card_owners.json")
        if not matches(STATE, inp.get("message", "")):
            return deny("message must be exactly the text render_msg.py prints")
        return True, "templated message"
    return deny(f"{tool} is not on the background WATCHER allow-list")


def log(tool, ok, why):
    try:
        d = os.path.join(STATE, "logs")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, f"guard-{datetime.now():%Y-%m-%d}.log"), "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%H:%M:%S} {'ALLOW' if ok else 'DENY '} {tool} | {why}\n")
    except OSError:
        pass


def main():
    try:
        event = json.load(sys.stdin)
        tool, inp = event.get("tool_name", ""), event.get("tool_input") or {}
        ok, why = check(tool, inp)
        target = inp.get("command") or inp.get("file_path") or inp.get("path") or inp.get("to") or ""
    except Exception as e:  # a broken guard must block, never allow
        tool, ok, why, target = "?", False, f"guard error: {type(e).__name__}", ""
    log(tool, ok, f"{why} | {' '.join(str(target).split())[:100]}" if target else why)
    if not ok:
        print(f"Blocked by the background WATCHER guard: {why}", file=sys.stderr)
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
