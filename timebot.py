#!/usr/bin/env python3
"""
Timebot - Daily Trello work summarizer for Savvy Otter
Reads the Trello board for cards updated today, parses [CLIENT/PROJECT] tags,
extracts time entries from comments, and generates CSV + Markdown reports.
"""

import os
import re
import csv
import logging
from datetime import datetime, date
from zoneinfo import ZoneInfo
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
TRELLO_API_KEY   = os.getenv("TRELLO_API_KEY")
TRELLO_API_TOKEN = os.getenv("TRELLO_API_TOKEN")
TRELLO_BOARD_ID  = os.getenv("TRELLO_BOARD_ID", "mdS3ny24")
CENTRAL_TZ       = ZoneInfo("America/Chicago")
BASE_DIR         = Path(__file__).parent
REPORTS_DIR      = BASE_DIR / "daily_reports"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("timebot")


# ── Trello helpers ─────────────────────────────────────────────────────────────
def trello_get(path: str, params: dict = None) -> any:
    base = {"key": TRELLO_API_KEY, "token": TRELLO_API_TOKEN}
    if params:
        base.update(params)
    r = requests.get(f"https://api.trello.com/1{path}", params=base, timeout=15)
    r.raise_for_status()
    return r.json()


def get_board_lists() -> dict:
    lists = trello_get(f"/boards/{TRELLO_BOARD_ID}/lists", {"filter": "all"})
    return {lst["id"]: lst["name"] for lst in lists}


def get_cards_updated_today(today: date) -> list:
    """Return all non-archived cards whose dateLastActivity falls on today (Central)."""
    cards = trello_get(
        f"/boards/{TRELLO_BOARD_ID}/cards",
        {"filter": "all", "fields": "name,idList,dateLastActivity,url,desc,labels"},
    )
    result = []
    for card in cards:
        last_activity = card.get("dateLastActivity")
        if not last_activity:
            continue
        dt = datetime.fromisoformat(last_activity.replace("Z", "+00:00"))
        if dt.astimezone(CENTRAL_TZ).date() == today:
            result.append(card)
    return result


def get_card_actions_today(card_id: str, today: date) -> list:
    """Return all commentCard / updateCard / createCard actions on a card from today."""
    actions = trello_get(
        f"/cards/{card_id}/actions",
        {"filter": "commentCard,updateCard,createCard", "limit": 100},
    )
    result = []
    for action in actions:
        dt = datetime.fromisoformat(action["date"].replace("Z", "+00:00"))
        if dt.astimezone(CENTRAL_TZ).date() == today:
            result.append(action)
    return result


# ── Parsing helpers ────────────────────────────────────────────────────────────
def parse_tag(card_name: str) -> tuple[str, str]:
    """
    Extract [TAG] from card names like '[Acme Corp] Fix login bug'.
    Returns (tag, title_without_tag).
    """
    m = re.match(r"^\[([^\]]+)\]\s*(.*)", card_name.strip())
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return "Uncategorized", card_name.strip()


def parse_time_minutes(text: str) -> int:
    """
    Parse time durations from free-form comment text.

    Recognized patterns (case-insensitive):
      2h  2hr  2hrs  2 hours  2.5h  2.5 hours
      30m  30min  30 mins  30 minutes  0.5h
      1:30  (H:MM treated as hours:minutes)

    Returns total minutes found, or 0 if nothing recognized.
    """
    total = 0.0
    lower = text.lower()

    # H:MM  (must NOT be preceded by digits — avoids matching timestamps)
    for m in re.finditer(r"(?<!\d)(\d+):(\d{2})(?!\d)", lower):
        total += int(m.group(1)) * 60 + int(m.group(2))

    # Nh / N.Nh / N hours
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*h(?:r|rs|our|ours)?(?!\w)", lower):
        total += float(m.group(1)) * 60

    # Nm / N.Nm / N min / N minutes
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*m(?:in|ins|inute|inutes)?(?!\w)", lower):
        total += float(m.group(1))

    return round(total)


def minutes_to_str(minutes: int) -> str:
    if not minutes:
        return "—"
    h, m = divmod(int(minutes), 60)
    if h and m:
        return f"{h}h {m}m"
    return f"{h}h" if h else f"{m}m"


# ── Card summarizer ────────────────────────────────────────────────────────────
def summarize_card(card: dict, actions: list, list_map: dict) -> dict:
    tag, title = parse_tag(card["name"])
    list_name   = list_map.get(card["idList"], "Unknown")
    comments    = []
    movements   = []
    total_mins  = 0

    for action in actions:
        atype = action["type"]
        if atype == "commentCard":
            text = action["data"].get("text", "")
            comments.append(text)
            total_mins += parse_time_minutes(text)
        elif atype == "updateCard":
            data = action["data"]
            if "listBefore" in data and "listAfter" in data:
                movements.append(
                    f"{data['listBefore']['name']} → {data['listAfter']['name']}"
                )
        elif atype == "createCard":
            movements.append("Card created")

    return {
        "tag":       tag,
        "title":     title,
        "full_name": card["name"],
        "list":      list_name,
        "url":       card["url"],
        "comments":  comments,
        "movements": movements,
        "minutes":   total_mins,
    }


# ── Report writers ─────────────────────────────────────────────────────────────
def write_csv(summaries: list, today: date) -> Path:
    REPORTS_DIR.mkdir(exist_ok=True)
    path = REPORTS_DIR / f"{today}.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Date", "Tag/Client", "Card Title", "Status/List",
                         "Time Logged", "Activity", "URL"])
        for s in summaries:
            writer.writerow([
                str(today),
                s["tag"],
                s["title"],
                s["list"],
                minutes_to_str(s["minutes"]),
                " | ".join(s["movements"]) if s["movements"] else "",
                s["url"],
            ])
    log.info("CSV  → %s", path)
    return path


def write_markdown(summaries: list, today: date) -> Path:
    REPORTS_DIR.mkdir(exist_ok=True)
    path = REPORTS_DIR / f"{today}.md"

    # Group by tag
    by_tag: dict[str, list] = {}
    for s in summaries:
        by_tag.setdefault(s["tag"], []).append(s)

    total_mins = sum(s["minutes"] for s in summaries)

    lines = [
        f"# Daily Work Report — {today}",
        "",
        f"> **Generated:** {datetime.now(CENTRAL_TZ).strftime('%Y-%m-%d %I:%M %p')} Central  ",
        f"> **Cards updated today:** {len(summaries)}  ",
        f"> **Total logged time:** {minutes_to_str(total_mins)}",
        "",
        "---",
        "",
    ]

    for tag in sorted(by_tag.keys()):
        cards     = by_tag[tag]
        tag_mins  = sum(c["minutes"] for c in cards)
        lines.append(f"## [{tag}]")
        lines.append(f"*{len(cards)} card(s) &nbsp;·&nbsp; {minutes_to_str(tag_mins)}*")
        lines.append("")

        for s in cards:
            lines.append(f"### {s['title']}")
            lines.append(f"- **Status:** {s['list']}")
            lines.append(f"- **Time Logged:** {minutes_to_str(s['minutes'])}")
            lines.append(f"- **Card URL:** {s['url']}")

            if s["movements"]:
                lines.append("- **Movements:**")
                for mv in s["movements"]:
                    lines.append(f"  - {mv}")

            if s["comments"]:
                lines.append("- **Comments today:**")
                for c in s["comments"]:
                    # Indent multi-line comments nicely
                    for line in c.strip().splitlines():
                        lines.append(f"  > {line}")

            lines.append("")

    # Summary table
    lines += [
        "---",
        "",
        "## Summary Table",
        "",
        "| Tag / Client | Card | Status | Time Logged |",
        "|---|---|---|---|",
    ]
    for s in summaries:
        lines.append(
            f"| {s['tag']} | [{s['title']}]({s['url']}) | {s['list']} | {minutes_to_str(s['minutes'])} |"
        )

    lines += [
        "",
        f"**Total Time: {minutes_to_str(total_mins)}**",
        "",
        "---",
        "*Report generated by [Timebot](https://github.com/Savvyotter/timebot)*",
    ]

    path.write_text("\n".join(lines), encoding="utf-8")
    log.info("MD   → %s", path)
    return path


# ── Main ───────────────────────────────────────────────────────────────────────
def run():
    if not TRELLO_API_KEY or not TRELLO_API_TOKEN:
        log.error("TRELLO_API_KEY and TRELLO_API_TOKEN must be set in .env")
        raise SystemExit(1)

    today = datetime.now(CENTRAL_TZ).date()
    log.info("Timebot starting — reporting for %s", today)

    list_map = get_board_lists()
    cards    = get_cards_updated_today(today)
    log.info("Cards updated today: %d", len(cards))

    summaries = []
    for card in cards:
        actions = get_card_actions_today(card["id"], today)
        summaries.append(summarize_card(card, actions, list_map))

    # Sort by tag, then card title
    summaries.sort(key=lambda s: (s["tag"].lower(), s["title"].lower()))

    csv_path = write_csv(summaries, today)
    md_path  = write_markdown(summaries, today)

    print(f"\n{'='*55}")
    print(f"  Timebot — {today}")
    print(f"  Cards processed : {len(summaries)}")
    print(f"  Total time      : {minutes_to_str(sum(s['minutes'] for s in summaries))}")
    print(f"  CSV report      : {csv_path}")
    print(f"  Markdown report : {md_path}")
    print(f"{'='*55}\n")
    return summaries


if __name__ == "__main__":
    run()
