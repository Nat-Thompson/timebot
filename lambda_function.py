"""
Timebot Lambda — Daily Trello work reporter for Savvy Otter
Triggered daily at 5:00 PM Central via EventBridge Scheduler.
Reads updated Trello cards, builds an HTML report, emails it via SES.

Card conventions:
  Name:        [CLIENT] Card title
  Labels:      Support - Warranty | Support - Non Warranty | Project
  Description: First line "Project - <name>" for Project-type cards
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

# ── Config ─────────────────────────────────────────────────────────────────────
TRELLO_API_KEY   = os.environ["TRELLO_API_KEY"]
TRELLO_API_TOKEN = os.environ["TRELLO_API_TOKEN"]
TRELLO_BOARD_ID  = os.environ.get("TRELLO_BOARD_ID", "mdS3ny24")
SES_FROM         = os.environ.get("SES_FROM", "TIMEBOT <noreply@savvyottermations.com>")
SES_TO           = os.environ.get("SES_TO", "nat.thompson@savvyotter.com")
SES_REGION       = os.environ.get("SES_REGION", "us-east-1")
CENTRAL_TZ       = ZoneInfo("America/Chicago")

# Display order for work types in the report
WORK_TYPE_ORDER = ["Support - Warranty", "Support - Non Warranty", "Project", "Operations", "Uncategorized"]

WORK_TYPE_STYLE = {
    "Support - Warranty":     ("wt-warranty",     "⚙ Support - Warranty"),
    "Support - Non Warranty": ("wt-nonwarranty",  "⚙ Support - Non Warranty"),
    "Project":                ("wt-project",      "◈ Project"),
    "Operations":             ("wt-operations",   "⚑ Operations"),
    "Uncategorized":          ("wt-uncategorized","⚠ Uncategorized"),
}


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


def parse_work_type(card: dict) -> str:
    """Return the work type from the card's Trello labels, or 'Uncategorized'."""
    known = {"Support - Warranty", "Support - Non Warranty", "Project"}
    for label in card.get("labels", []):
        name = label.get("name", "").strip()
        if name in known:
            return name
    return "Uncategorized"


def parse_project_name(card: dict) -> str | None:
    """
    For Project-type cards, read the first line of the description for:
      Project - <name>
    Returns the project name string, or None if not found.
    """
    desc = (card.get("desc") or "").strip()
    if not desc:
        return None
    first_line = desc.splitlines()[0].strip()
    m = re.match(r"^Project\s*-\s*(.+)$", first_line, re.IGNORECASE)
    return m.group(1).strip() if m else None


def parse_time_minutes(text: str) -> int:
    """
    Parse explicit duration notation only — ignores clock times (8:30 am, 10:00 - 10:20).
    Recognized: 2h, 2hr, 2hrs, 2 hours, 1.5h, .5 hours, 30m, 30 min, 20 minutes, 0.25 hours
    """
    total = 0.0
    lower = text.lower()
    for m in re.finditer(r"(?<!\d)(\d*\.\d+|\d+)\s*h(?:r|rs|our|ours)?(?!\w)", lower):
        total += float(m.group(1)) * 60
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
    work_type   = parse_work_type(card)
    project     = parse_project_name(card) if work_type == "Project" else None
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
        "tag":        tag,
        "title":      title,
        "work_type":  work_type,
        "project":    project,
        "list":       list_map.get(card["idList"], "Unknown"),
        "url":        card["url"],
        "comments":   comments,
        "movements":  movements,
        "minutes":    total_mins,
    }


# ── HTML builder ───────────────────────────────────────────────────────────────
def build_html(summaries: list, today: date) -> str:
    generated  = datetime.now(CENTRAL_TZ).strftime("%Y-%m-%d %I:%M %p")
    total_mins = sum(s["minutes"] for s in summaries)

    # Group: client → work_type → project (for Project type) → cards
    by_client: dict[str, dict[str, dict[str, list]]] = {}
    for s in summaries:
        wt  = s["work_type"]
        prj = s["project"] or "(No project name)"
        by_client.setdefault(s["tag"], {}).setdefault(wt, {}).setdefault(prj, []).append(s)

    def esc(t: str) -> str:
        return (t.replace("&", "&amp;").replace("<", "&lt;")
                 .replace(">", "&gt;").replace('"', "&quot;"))

    def card_html(s: dict) -> str:
        movements_html = ""
        if s["movements"]:
            items = "".join(f"<li>{esc(mv)}</li>" for mv in s["movements"])
            movements_html = f"<div class='meta-label'>Movements</div><ul class='movements'>{items}</ul>"
        comments_html = ""
        if s["comments"]:
            items = "".join(f"<blockquote>{esc(c.strip())}</blockquote>" for c in s["comments"])
            comments_html = f"<div class='meta-label'>Comments</div>{items}"
        time_cls = "time-logged" if s["minutes"] else "time-none"
        return f"""
        <div class='card'>
          <div class='card-header'>
            <span class='card-title'><a href='{esc(s["url"])}' target='_blank'>{esc(s["title"])}</a></span>
            <span class='{time_cls}'>{esc(minutes_to_str(s["minutes"]))}</span>
          </div>
          <div class='card-meta'><span class='status-badge'>{esc(s["list"])}</span></div>
          {movements_html}{comments_html}
        </div>"""

    # ── Client sections ────────────────────────────────────────────────────────
    sections = ""
    for tag in sorted(by_client.keys()):
        wt_map   = by_client[tag]
        tag_mins = sum(s["minutes"] for wt in wt_map.values() for prj in wt.values() for s in prj)

        wt_blocks = ""
        for wt in WORK_TYPE_ORDER:
            if wt not in wt_map:
                continue
            prj_map  = wt_map[wt]
            wt_mins  = sum(s["minutes"] for prj in prj_map.values() for s in prj)
            css_cls, wt_label = WORK_TYPE_STYLE[wt]

            if wt == "Project":
                prj_blocks = ""
                for prj_name in sorted(prj_map.keys()):
                    cards      = prj_map[prj_name]
                    prj_mins   = sum(s["minutes"] for s in cards)
                    cards_html = "".join(card_html(s) for s in cards)
                    prj_blocks += f"""
                    <div class='project-group'>
                      <div class='project-header'>
                        <span class='project-name'>◈ {esc(prj_name)}</span>
                        <span class='project-time'>{esc(minutes_to_str(prj_mins))}</span>
                      </div>
                      {cards_html}
                    </div>"""
                inner = prj_blocks
            else:
                inner = "".join(card_html(s) for prj in prj_map.values() for s in prj)

            wt_blocks += f"""
            <div class='wt-section {css_cls}'>
              <div class='wt-header'>
                <span class='wt-label'>{wt_label}</span>
                <span class='wt-time'>{esc(minutes_to_str(wt_mins))}</span>
              </div>
              {inner}
            </div>"""

        sections += f"""
        <div class='tag-section'>
          <div class='tag-header'>
            <span class='tag-name'>[{esc(tag)}]</span>
            <span class='tag-meta'>{sum(len(p) for wt in wt_map.values() for p in wt.values())} card(s) &nbsp;·&nbsp; {esc(minutes_to_str(tag_mins))}</span>
          </div>
          {wt_blocks}
        </div>"""

    # ── Work-type summary table ────────────────────────────────────────────────
    wt_totals: dict[str, int] = {}
    for s in summaries:
        wt_totals[s["work_type"]] = wt_totals.get(s["work_type"], 0) + s["minutes"]

    wt_rows = ""
    for wt in WORK_TYPE_ORDER:
        if wt not in wt_totals:
            continue
        css_cls, wt_label = WORK_TYPE_STYLE[wt]
        wt_rows += f"<tr><td><span class='wt-pill {css_cls}'>{wt_label}</span></td><td class='time-cell'>{esc(minutes_to_str(wt_totals[wt]))}</td></tr>"

    # ── Project summary table ──────────────────────────────────────────────────
    project_totals: dict[tuple, int] = {}
    for s in summaries:
        if s["work_type"] == "Project":
            key = (s["tag"], s["project"] or "(No project name)")
            project_totals[key] = project_totals.get(key, 0) + s["minutes"]

    prj_rows = ""
    if project_totals:
        for (client, prj), mins in sorted(project_totals.items()):
            prj_rows += f"<tr><td>{esc(prj)}</td><td><span class='tag-pill'>{esc(client)}</span></td><td class='time-cell'>{esc(minutes_to_str(mins))}</td></tr>"
        prj_total = sum(project_totals.values())
        prj_rows += f"<tr class='total-row'><td colspan='2'>Total Project Time</td><td class='time-cell'>{esc(minutes_to_str(prj_total))}</td></tr>"

    project_table = ""
    if prj_rows:
        project_table = f"""
        <div class='summary-section'>
          <h2>Project Summary</h2>
          <table>
            <thead><tr><th>Project</th><th>Client</th><th>Time</th></tr></thead>
            <tbody>{prj_rows}</tbody>
          </table>
        </div>"""

    # ── Card-level summary table ───────────────────────────────────────────────
    card_rows = ""
    for s in summaries:
        css_cls, wt_label = WORK_TYPE_STYLE[s["work_type"]]
        prj_cell = esc(s["project"]) if s["project"] else "<span style='color:#9ca3af'>—</span>"
        card_rows += f"""
        <tr>
          <td><span class='tag-pill'>{esc(s["tag"])}</span></td>
          <td><a href='{esc(s["url"])}' target='_blank'>{esc(s["title"])}</a></td>
          <td><span class='wt-pill {css_cls}'>{wt_label}</span></td>
          <td>{prj_cell}</td>
          <td>{esc(s["list"])}</td>
          <td class='time-cell'>{esc(minutes_to_str(s["minutes"]))}</td>
        </tr>"""
    card_rows += f"<tr class='total-row'><td colspan='5'>Total</td><td class='time-cell'>{esc(minutes_to_str(total_mins))}</td></tr>"

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
    .kpi-bar {{ display: flex; gap: 1.5rem; margin-top: 1rem; flex-wrap: wrap; }}
    .kpi {{ background: rgba(255,255,255,.1); border-radius: 8px; padding: .5rem 1rem; font-size: .9rem; }}
    .kpi strong {{ display: block; font-size: 1.3rem; }}

    .content {{ max-width: 980px; margin: 2rem auto; padding: 0 1.5rem 3rem; }}

    /* Client sections */
    .tag-section {{ background: #fff; border-radius: 10px; margin-bottom: 1.5rem;
                    box-shadow: 0 1px 4px rgba(0,0,0,.08); overflow: hidden; }}
    .tag-header {{ background: #1a1a2e; color: #fff; padding: .75rem 1.25rem;
                   display: flex; justify-content: space-between; align-items: center; }}
    .tag-name {{ font-weight: 700; font-size: 1rem; }}
    .tag-meta {{ font-size: .8rem; opacity: .75; }}

    /* Work-type sub-sections */
    .wt-section {{ border-top: 1px solid #f0f0f0; }}
    .wt-header {{ display: flex; justify-content: space-between; align-items: center;
                  padding: .5rem 1.25rem; font-size: .82rem; font-weight: 600; }}
    .wt-time {{ font-weight: 600; }}
    .wt-warranty   .wt-header {{ background: #fffbeb; color: #92400e; }}
    .wt-nonwarranty .wt-header {{ background: #eff6ff; color: #1e40af; }}
    .wt-project    .wt-header {{ background: #f0fdf4; color: #166534; }}
    .wt-operations    .wt-header {{ background: #faf5ff; color: #6b21a8; }}
    .wt-uncategorized .wt-header {{ background: #f9fafb; color: #6b7280; }}

    /* Project sub-groups */
    .project-group {{ border-top: 1px dashed #e5e7eb; margin: 0 1.25rem; }}
    .project-header {{ display: flex; justify-content: space-between; align-items: center;
                       padding: .4rem 0; font-size: .85rem; }}
    .project-name {{ font-weight: 600; color: #166534; }}
    .project-time {{ font-size: .8rem; color: #6b7280; }}

    /* Cards */
    .card {{ padding: .75rem 1.25rem; border-top: 1px solid #f5f5f5; }}
    .card-header {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem; }}
    .card-title {{ font-weight: 600; font-size: .9rem; }}
    .time-logged {{ background: #d1fae5; color: #065f46; font-size: .78rem;
                    font-weight: 600; padding: .2rem .55rem; border-radius: 99px; white-space: nowrap; }}
    .time-none {{ color: #9ca3af; font-size: .78rem; white-space: nowrap; }}
    .card-meta {{ margin-top: .25rem; }}
    .status-badge {{ background: #e0e7ff; color: #3730a3; font-size: .72rem; padding: .15rem .45rem; border-radius: 4px; }}
    .meta-label {{ font-size: .72rem; font-weight: 600; color: #9ca3af;
                   text-transform: uppercase; letter-spacing: .05em; margin-top: .6rem; margin-bottom: .2rem; }}
    .movements {{ padding-left: 1.1rem; font-size: .82rem; color: #4b5563; }}
    .movements li {{ margin-bottom: .1rem; }}
    blockquote {{ border-left: 3px solid #e0e7ff; padding: .35rem .75rem; margin: .2rem 0;
                  font-size: .82rem; color: #374151; background: #f9fafb; border-radius: 0 4px 4px 0; }}

    /* Summary tables */
    .summary-section {{ margin-top: 2rem; }}
    .summary-section h2 {{ font-size: 1.05rem; font-weight: 600; margin-bottom: .6rem; color: #1a1a2e; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff; border-radius: 10px;
             overflow: hidden; box-shadow: 0 1px 4px rgba(0,0,0,.08); margin-bottom: 1.5rem; }}
    th {{ background: #1a1a2e; color: #fff; padding: .55rem 1rem; font-size: .78rem; text-align: left; font-weight: 600; }}
    td {{ padding: .6rem 1rem; font-size: .83rem; border-bottom: 1px solid #f0f0f0; vertical-align: middle; }}
    tr:last-child td {{ border-bottom: none; }}
    .total-row td {{ font-weight: 700; background: #f9fafb; }}
    .time-cell {{ font-weight: 600; color: #065f46; }}
    .tag-pill {{ background: #eef2ff; color: #3730a3; font-size: .72rem;
                 font-weight: 600; padding: .15rem .45rem; border-radius: 4px; }}
    .wt-pill {{ font-size: .72rem; font-weight: 600; padding: .15rem .45rem; border-radius: 4px; }}
    .wt-pill.wt-warranty     {{ background: #fef3c7; color: #92400e; }}
    .wt-pill.wt-nonwarranty  {{ background: #dbeafe; color: #1e40af; }}
    .wt-pill.wt-project      {{ background: #d1fae5; color: #065f46; }}
    .wt-pill.wt-operations    {{ background: #f3e8ff; color: #6b21a8; }}
    .wt-pill.wt-uncategorized {{ background: #f3f4f6; color: #6b7280; }}

    footer {{ text-align: center; font-size: .72rem; color: #9ca3af; margin-top: 1rem; padding-bottom: 2rem; }}
  </style>
</head>
<body>
  <div class="page-header">
    <h1>TIMEBOT &mdash; {today}</h1>
    <div class="meta">Generated {generated} Central</div>
    <div class="kpi-bar">
      <div class="kpi"><strong>{len(summaries)}</strong>Cards</div>
      <div class="kpi"><strong>{esc(minutes_to_str(total_mins))}</strong>Total Time</div>
      <div class="kpi"><strong>{len(by_client)}</strong>Clients</div>
      <div class="kpi"><strong>{len(project_totals)}</strong>Projects</div>
    </div>
  </div>

  <div class="content">
    {sections}

    <div class="summary-section">
      <h2>Time by Work Type</h2>
      <table>
        <thead><tr><th>Work Type</th><th>Time</th></tr></thead>
        <tbody>
          {wt_rows}
          <tr class="total-row"><td>Total</td><td class="time-cell">{esc(minutes_to_str(total_mins))}</td></tr>
        </tbody>
      </table>
    </div>

    {project_table}

    <div class="summary-section">
      <h2>All Cards</h2>
      <table>
        <thead><tr><th>Client</th><th>Card</th><th>Type</th><th>Project</th><th>Status</th><th>Time</th></tr></thead>
        <tbody>{card_rows}</tbody>
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

    summaries.sort(key=lambda s: (s["tag"].lower(), s["work_type"], s["title"].lower()))

    total_mins = sum(s["minutes"] for s in summaries)
    subject    = f"TIMEBOT | {today} | {len(summaries)} cards | {minutes_to_str(total_mins)}"
    html       = build_html(summaries, today)
    send_email(subject, html)

    log.info("Done — %d cards, %s", len(summaries), minutes_to_str(total_mins))
    return {"statusCode": 200, "cards": len(summaries), "totalTime": minutes_to_str(total_mins)}
