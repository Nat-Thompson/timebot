"""The only messages the background WATCHER may send to other Claude sessions.

Every message is rendered from a fixed template plus a validated reference (a Trello shortlink, a
Gmail thread id, a line number). No text from email, Teams or a web page is ever copied into one,
so an injected instruction in an email cannot ride a message into a session that has write powers.
guard.py re-renders the message and denies SendMessage unless the text matches exactly.

Session names (e.g. "WATCHER [6997e7]") come from state/recipients.json and state/card_owners.json,
both kept by the interactive WATCHER. The background job may read them but never write them.
"""
import json
import os
import re

REFS = {
    "shortlink": re.compile(r"^[A-Za-z0-9]{8}$"),
    "thread": re.compile(r"^[0-9a-f]{16}$"),
    "line": re.compile(r"^[0-9]{1,4}$"),
}

TEMPO = ("TEMPO: log this session's time on Items card https://trello.com/c/{ref}. One comment per work day, "
         "FIRST LINE exactly `[date: YYYY-MM-DD] <short summary> - <N> minutes`, measured with the 20-second cap "
         "on every transcript gap, with no other duration tokens on line 1 and no em dashes. Read the card's "
         "comments first so you never double-log. Post with Python urllib and a JSON body (watcher/post_comment.py "
         "or edit_comment.py), read it back, and assert no %XX survived. If the write is blocked, send the line to "
         "{watcher} to post. Log on the Items card, not the Dev Ops card.")

TEMPLATES = {
    # Dev Ops card landed in Deploy To Staging Request -> OTTER-OPS starts the review.
    "STAGING": ("shortlink", "WATCHER-BG | STAGING | {ref} | Dev Ops card https://trello.com/c/{ref} is in Deploy To "
                             "Staging Request. Please take it per the Dev Ops playbook (move it to Reviewing PR and "
                             "start the review). Reply to {watcher} with one line when it is in Reviewing PR."),
    # A PR whose project already has a review session: OTTER-OPS hands the review there.
    "PR-OWNER": ("shortlink", "WATCHER-BG | PR-OWNER | {ref} | Dev Ops card https://trello.com/c/{ref} is a PR review "
                              "request for a project that already has a review session: {owner}. Please hand the "
                              "review there instead of starting a new chip."),
    # An Items card moved into Doing and this session owns it.
    "CARD": ("shortlink", "WATCHER-BG | CARD | {ref} | Items card https://trello.com/c/{ref} is in Doing and the "
                          "board says it is yours. Pick it up when you are free and reply to {watcher} with one line "
                          "on its state.\n\n" + TEMPO),
    # A new message on a Gmail thread this session asked WATCHER to watch (state/thread_watches.txt).
    "REPLY": ("thread", "WATCHER-BG | REPLY | {ref} | A new message arrived on Gmail thread {ref}, which you asked "
                        "WATCHER to watch. Read it yourself with get_thread {ref} and handle it per your task. "
                        "Reply to {watcher} with one line when you are done with the watch."),
    # A scheduled check for this session is due (line N of state/scheduled_checks.txt).
    "CHECK": ("line", "WATCHER-BG | CHECK | {ref} | A scheduled check for you is due: line {ref} of "
                      "C:\\git\\savvyotter\\timebot\\watcher\\state\\scheduled_checks.txt. Read that line and do the "
                      "read-only check it describes. Reply to {watcher} with the result in one line."),
    # A new Landscape Patrol/Patch/Reboot/Backup digest email -> LANDSCAPE triages it.
    "DIGEST": ("thread", "WATCHER-BG | DIGEST | {ref} | A new Landscape digest email arrived (Gmail thread {ref}). "
                         "Please read it and triage any NEW condition, and send FLEET-ISSUE lines to {watcher}."),
}


def _load(state, name):
    try:
        with open(os.path.join(state, name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def recipients(state):
    """Every session name the background job may message: the roster plus card owners."""
    roster = _load(state, "recipients.json")
    owners = _load(state, "card_owners.json")
    return {v for v in list(roster.values()) + list(owners.values()) if isinstance(v, str) and v.strip()}


def watcher_name(state):
    return _load(state, "recipients.json").get("WATCHER", "WATCHER")


def render(state, kind, ref, owner=None):
    """The exact message text, or raise ValueError. owner is a key of state/recipients.json."""
    if kind not in TEMPLATES:
        raise ValueError(f"unknown kind {kind}")
    reftype, text = TEMPLATES[kind]
    if not REFS[reftype].match(ref or ""):
        raise ValueError(f"{kind} needs a {reftype} ref, got {ref!r}")
    roster = _load(state, "recipients.json")
    if "{owner}" in text:
        if owner not in roster:
            raise ValueError(f"owner {owner!r} is not a key in recipients.json")
    elif owner:
        raise ValueError(f"{kind} takes no owner")
    return text.format(ref=ref, owner=roster.get(owner, ""), watcher=watcher_name(state))


def matches(state, message):
    """True if message is exactly some template rendered with a valid ref (and owner)."""
    m = re.match(r"^WATCHER-BG \| ([A-Z-]+) \| ([^ |]+) \|", message or "")
    if not m:
        return False
    owners = [None] + sorted(_load(state, "recipients.json"))
    for owner in owners:
        try:
            if render(state, m.group(1), m.group(2), owner) == message:
                return True
        except ValueError:
            continue
    return False
