"""
Timebot Lambda — Daily Trello work reporter for Savvy Otter
Triggered daily at 5:00 PM Central via EventBridge Scheduler.
Reads updated Trello cards, builds an HTML report, emails it via SES.
"""

import os
import re
import logging
from datetime import datetime, date
from zoneinfo import ZoneInfo

import boto3
import requests

log = logging.getLogger()
log.setLevel(logging.INFO)

# ── Config (Lambda environment variables) ─────────────────────────────────────
TRELLO_API_KEY  = os.environ["TRELLO_API_KEY"]
TRELLO_API_TOKEN = os.environ["TRELLO_API_TOKEN"]
TRELLO_BOARD_ID = os.environ.get("TRELLO_BOARD_ID", "mdS3ny24")
SES_FROM        = os.environ.get("SES_FROM", "TIMEBOT <noreply@savvyottermations.com>")
SES_TO          = os.environ.get("SES_TO", "nat.thompson@savvyotter.com")
SES_REGION      = os.environ.get("SES_REGION", "us-east-1")
CENTRAL_TZ      = ZoneInfo("America/Chicago")


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
    cards = trello_get(
        f"/boards/{TRELLO_BOARD_ID}/cards",
        {"filter": "all", "fields": "name,idList,dateLastActivity,url,desc,labels"},
    )
    return [
        c for c in cards
        if c.get("dateLastActivity") and
        datetime.fromisoformat(c["dateLastActivity"].replace("Z", "+00:00"))
               .astimezone(CENTRAL_TZ).date() == today
    ]


def get_card_actions_today(card_id: str, today: date) -> list:
    actions = trello_get(
        f"/cards/{card_id}/actions",
        {"filter": "commentCard,updateCard,createCard", "limit": 100},
    )
    return [
        a for a in actions
        if datetime.fromisoformat(a["date"].replace("Z", "+00:00"))
                   .astimezone(CENTRAL_TZ).date() == today
    ]


# ── Parsing helpers ────────────────────────────────────────────────────────────
def parse_tag(card_name: str) -> tuple[str, str]:
    m = re.match(r"^\[([^\]]+)\]\s*(.*)", card_name.strip())
    return (m.group(1).strip(), m.group(2).strip()) if m else ("Uncategorized", card_name.strip())


def parse_time_minutes(text: str) -> int:
    """
    Parse explicit duration notation only — ignores clock times (8:30 am, 10:00 - 10:20).
    Recognized: 2h, 2hr, 2hrs, 2 hours, 1.5h, .5 hours, 30m, 30 min, 20 minutes, 0.25 hours
    """
    total = 0.0
    lower = text.lower()
    # Hours: 2h / 1.5h / .5 hours / 2 hours etc.
    for m in re.finditer(r"(?<!\d)(\d*\.\d+|\d+)\s*h(?:r|rs|our|ours)?(?!\w)", lower):
        total += float(m.group(1)) * 60
    # Minutes: 30m / 20 min / 20 minutes etc.
    for m in re.finditer(r"(?<!\d)(\d*\.\d+|\d+)\s*m(?:in|ins|inute|inutes)?(?!\w)", lower):
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
    tag, title  = parse_tag(card["name"])
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
                movements.append(f"{data['listBefore']['name']} → {data['listAfter']['name']}")
        elif atype == "createCard":
            movements.append("Card created")

    return {
        "tag":       tag,
        "title":     title,
        "list":      list_map.get(card["idList"], "Unknown"),
        "url":       card["url"],
        "comments":  comments,
        "movements": movements,
        "minutes":   total_mins,
    }


# ── HTML builder ───────────────────────────────────────────────────────────────
def build_html(summaries: list, today: date) -> str:
    by_tag: dict[str, list] = {}
    for s in summaries:
        by_tag.setdefault(s["tag"], []).append(s)

    total_mins = sum(s["minutes"] for s in summaries)
    generated  = datetime.now(CENTRAL_TZ).strftime("%Y-%m-%d %I:%M %p")

    def esc(t: str) -> str:
        return (t.replace("&", "&amp;").replace("<", "&lt;")
                 .replace(">", "&gt;").replace('"', "&quot;"))

    sections = ""
    for tag in sorted(by_tag.keys()):
        cards    = by_tag[tag]
        tag_mins = sum(c["minutes"] for c in cards)

        cards_html = ""
        for s in cards:
            movements_html = ""
            if s["movements"]:
                items = "".join(f"<li>{esc(mv)}</li>" for mv in s["movements"])
                movements_html = f"<div class='meta-label'>Movements</div><ul class='movements'>{items}</ul>"

            comments_html = ""
            if s["comments"]:
                items = "".join(f"<blockquote>{esc(c.strip())}</blockquote>" for c in s["comments"])
                comments_html = f"<div class='meta-label'>Comments</div>{items}"

            time_cls = "time-logged" if s["minutes"] else "time-none"
            cards_html += f"""
            <div class='card'>
              <div class='card-header'>
                <span class='card-title'><a href='{esc(s["url"])}' target='_blank'>{esc(s["title"])}</a></span>
                <span class='{time_cls}'>{esc(minutes_to_str(s["minutes"]))}</span>
              </div>
              <div class='card-meta'><span class='status-badge'>{esc(s["list"])}</span></div>
              {movements_html}
              {comments_html}
            </div>"""

        sections += f"""
        <div class='tag-section'>
          <div class='tag-header'>
            <span class='tag-name'>[{esc(tag)}]</span>
            <span class='tag-meta'>{len(cards)} card(s) &nbsp;·&nbsp; {esc(minutes_to_str(tag_mins))}</span>
          </div>
          {cards_html}
        </div>"""

    rows = ""
    for s in summaries:
        rows += f"""
        <tr>
          <td><span class='tag-pill'>{esc(s["tag"])}</span></td>
          <td><a href='{esc(s["url"])}' target='_blank'>{esc(s["title"])}</a></td>
          <td>{esc(s["list"])}</td>
          <td class='time-cell'>{esc(minutes_to_str(s["minutes"]))}</td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>TIMEBOT — {today}</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            background: #f4f6f9; color: #1a1a2e; line-height: 1.5; }}
    a {{ color: #0066cc; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .page-header {{ background: #1a1a2e; color: #fff; padding: 2rem 2.5rem; }}
    .page-header h1 {{ font-size: 1.6rem; font-weight: 600; }}
    .page-header .meta {{ margin-top: .4rem; font-size: .85rem; opacity: .75; }}
    .kpi-bar {{ display: flex; gap: 1.5rem; margin-top: 1rem; }}
    .kpi {{ background: rgba(255,255,255,.1); border-radius: 8px; padding: .5rem 1rem; font-size: .9rem; }}
    .kpi strong {{ display: block; font-size: 1.3rem; }}
    .content {{ max-width: 960px; margin: 2rem auto; padding: 0 1.5rem 3rem; }}
    .tag-section {{ background: #fff; border-radius: 10px; margin-bottom: 1.5rem;
                    box-shadow: 0 1px 4px rgba(0,0,0,.08); overflow: hidden; }}
    .tag-header {{ background: #eef2ff; padding: .75rem 1.25rem;
                   display: flex; justify-content: space-between; align-items: center; }}
    .tag-name {{ font-weight: 700; font-size: 1rem; color: #3730a3; }}
    .tag-meta {{ font-size: .8rem; color: #6b7280; }}
    .card {{ padding: 1rem 1.25rem; border-top: 1px solid #f0f0f0; }}
    .card-header {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem; }}
    .card-title {{ font-weight: 600; font-size: .95rem; }}
    .time-logged {{ background: #d1fae5; color: #065f46; font-size: .8rem;
                    font-weight: 600; padding: .2rem .6rem; border-radius: 99px; white-space: nowrap; }}
    .time-none {{ color: #9ca3af; font-size: .8rem; white-space: nowrap; }}
    .card-meta {{ margin-top: .3rem; }}
    .status-badge {{ background: #e0e7ff; color: #3730a3; font-size: .75rem; padding: .15rem .5rem; border-radius: 4px; }}
    .meta-label {{ font-size: .75rem; font-weight: 600; color: #6b7280;
                   text-transform: uppercase; letter-spacing: .05em; margin-top: .75rem; margin-bottom: .25rem; }}
    .movements {{ padding-left: 1.2rem; font-size: .85rem; color: #4b5563; }}
    .movements li {{ margin-bottom: .15rem; }}
    blockquote {{ border-left: 3px solid #e0e7ff; padding: .4rem .8rem; margin: .25rem 0;
                  font-size: .85rem; color: #374151; background: #f9fafb; border-radius: 0 4px 4px 0; }}
    .summary-section {{ margin-top: 2.5rem; }}
    .summary-section h2 {{ font-size: 1.1rem; font-weight: 600; margin-bottom: .75rem; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff; border-radius: 10px;
             overflow: hidden; box-shadow: 0 1px 4px rgba(0,0,0,.08); }}
    th {{ background: #1a1a2e; color: #fff; padding: .6rem 1rem; font-size: .8rem; text-align: left; font-weight: 600; }}
    td {{ padding: .65rem 1rem; font-size: .85rem; border-bottom: 1px solid #f0f0f0; }}
    tr:last-child td {{ border-bottom: none; }}
    .tag-pill {{ background: #eef2ff; color: #3730a3; font-size: .75rem; font-weight: 600; padding: .15rem .5rem; border-radius: 4px; }}
    .time-cell {{ font-weight: 600; color: #065f46; }}
    .total-row td {{ font-weight: 700; background: #f9fafb; }}
    footer {{ text-align: center; font-size: .75rem; color: #9ca3af; margin-top: 2rem; padding-bottom: 2rem; }}
  </style>
</head>
<body>
  <div class="page-header">
    <h1>TIMEBOT &mdash; {today}</h1>
    <div class="meta">Generated {generated} Central</div>
    <div class="kpi-bar">
      <div class="kpi"><strong>{len(summaries)}</strong>Cards</div>
      <div class="kpi"><strong>{esc(minutes_to_str(total_mins))}</strong>Total Time</div>
      <div class="kpi"><strong>{len(by_tag)}</strong>Clients</div>
    </div>
  </div>
  <div class="content">
    {sections}
    <div class="summary-section">
      <h2>Summary Table</h2>
      <table>
        <thead><tr><th>Client</th><th>Card</th><th>Status</th><th>Time</th></tr></thead>
        <tbody>
          {rows}
          <tr class="total-row"><td colspan="3">Total</td><td class="time-cell">{esc(minutes_to_str(total_mins))}</td></tr>
        </tbody>
      </table>
    </div>
  </div>
  <footer>Generated by TIMEBOT</footer>
</body>
</html>"""


# ── Email ──────────────────────────────────────────────────────────────────────
def send_email(subject: str, html_body: str) -> None:
    client = boto3.client("sesv2", region_name=SES_REGION)
    client.send_email(
        FromEmailAddress=SES_FROM,
        Destination={"ToAddresses": [SES_TO]},
        Content={
            "Simple": {
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body":    {"Html": {"Data": html_body, "Charset": "UTF-8"}},
            }
        },
    )
    log.info("Email sent → %s", SES_TO)


# ── Handler ────────────────────────────────────────────────────────────────────
def lambda_handler(event, context):
    today    = datetime.now(CENTRAL_TZ).date()
    log.info("Timebot starting — reporting for %s", today)

    list_map  = get_board_lists()
    cards     = get_cards_updated_today(today)
    log.info("Cards updated today: %d", len(cards))

    summaries = []
    for card in cards:
        actions = get_card_actions_today(card["id"], today)
        summaries.append(summarize_card(card, actions, list_map))

    summaries.sort(key=lambda s: (s["tag"].lower(), s["title"].lower()))

    total_mins = sum(s["minutes"] for s in summaries)
    subject    = f"TIMEBOT | {today} | {len(summaries)} cards | {minutes_to_str(total_mins)}"
    html       = build_html(summaries, today)
    send_email(subject, html)

    log.info("Done — %d cards, %s", len(summaries), minutes_to_str(total_mins))
    return {"statusCode": 200, "cards": len(summaries), "totalTime": minutes_to_str(total_mins)}
