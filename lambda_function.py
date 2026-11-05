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
from datetime import datetime, date, timedelta
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
    result = []
    for a in actions:
        system_date = (
            datetime.fromisoformat(a["date"].replace("Z", "+00:00"))
            .astimezone(CENTRAL_TZ).date()
        )
        if a["type"] == "commentCard":
            backdate = parse_backdate(a["data"].get("text", ""))
            effective_date = backdate if backdate else system_date
        else:
            effective_date = system_date
        if effective_date == today:
            result.append(a)
    return result


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


def parse_backdate(text: str):
    """
    Extract [date: YYYY-MM-DD] from a comment body, if present.
    Returns a date object or None.
    Example: '[date: 2026-03-18] 6h SavvySync work'
    """
    from datetime import date as _date
    m = re.search(r'\[date:\s*(\d{4}-\d{2}-\d{2})\]', text, re.IGNORECASE)
    return _date.fromisoformat(m.group(1)) if m else None


def parse_time_minutes(text: str) -> int:
    """
    Parse explicit duration notation only — ignores clock times (8:30 am, 10:00 - 10:20).
    Recognized: 2h, 2hr, 2hrs, 2 hours, 1.5h, .5 hours, 30m, 30 min, 20 minutes, 0.25 hours
    Also strips [date: ...] tags before parsing so they don't confuse the regex.
    """
    text = re.sub(r'\[date:\s*\d{4}-\d{2}-\d{2}\]', '', text, flags=re.IGNORECASE)
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


# ── Morning report (event {"report": "morning"}) ──────────────────────────────
# Counts Tempo comments in Nat's standard format, keyed on the [date:] work date:
#   line 1 = "[date: YYYY-MM-DD] <summary> - <N> minutes"
# Comments without a [date:] first line are not Tempo and are ignored.
TEMPO_LINE_RE = re.compile(r"^\s*\[date:\s*(\d{4}-\d{2}-\d{2})\]\s*(.*)$", re.IGNORECASE)
TEMPO_MINS_RE = re.compile(r"-\s*(\d+(?:\.\d+)?)\s*min(?:ute)?s?\b", re.IGNORECASE)

# Trello label -> (report label, billable?). Legacy "Support - Warranty" means billable
# and "Support - Non Warranty" means non-billable (see ALEXANDRIA tempo.md).
LABEL_MAP = {
    "Project":                ("project",             True),
    "Support - Billable":     ("support-billable",    True),
    "Support - Warranty":     ("support-billable",    True),
    "Change Order":           ("change-order",        True),
    "Retainer":               ("retainer",            True),
    "Support - Non Billable": ("support-nonbillable", False),
    "Support - Non Warranty": ("support-nonbillable", False),
    "Operations":             ("operations",          False),
}
LABEL_ORDER = ["project", "support-billable", "change-order", "retainer",
               "support-nonbillable", "operations", "unlabeled"]


def parse_tempo_line(text: str):
    """Return (work_date, summary, minutes) from a Tempo comment, or None if not Tempo.
    minutes is None when the line has a [date:] but no '- N minutes'."""
    first = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    m = TEMPO_LINE_RE.match(first)
    if not m:
        return None
    rest = m.group(2)
    hits = list(TEMPO_MINS_RE.finditer(rest))
    if not hits:
        return date.fromisoformat(m.group(1)), rest.strip(), None
    last = hits[-1]
    return date.fromisoformat(m.group(1)), rest[:last.start()].strip(), round(float(last.group(1)))


def card_label(card: dict) -> tuple[str, bool]:
    for label in card.get("labels", []):
        if label.get("name", "").strip() in LABEL_MAP:
            return LABEL_MAP[label["name"].strip()]
    return ("unlabeled", False)


def morning_windows(today: date) -> dict:
    """Yesterday (Fri to Sun on a Monday), week to date (Mon of yesterday's week),
    month to date (1st of yesterday's month). All inclusive, Central dates."""
    end = today - timedelta(days=1)
    y_start = today - timedelta(days=3) if today.weekday() == 0 else end
    return {
        "yesterday": (y_start, end),
        "week":      (end - timedelta(days=end.weekday()), end),
        "month":     (end.replace(day=1), end),
    }


def get_board_comments_since(since: date) -> list:
    """All commentCard actions on the board posted on/after `since` (Central), paginated."""
    since_utc = datetime.combine(since, datetime.min.time(), CENTRAL_TZ).astimezone(ZoneInfo("UTC"))
    out, before = [], None
    while True:
        params = {"filter": "commentCard", "limit": 1000, "since": since_utc.strftime("%Y-%m-%dT%H:%M:%SZ")}
        if before:
            params["before"] = before
        page = trello_get(f"/boards/{TRELLO_BOARD_ID}/actions", params)
        out.extend(page)
        if len(page) < 1000:
            return out
        before = page[-1]["id"]


def collect_tempo(today: date) -> tuple[list, list, dict]:
    windows = morning_windows(today)
    start = min(w[0] for w in windows.values())
    end = max(w[1] for w in windows.values())
    cards = {c["id"]: c for c in trello_get(
        f"/boards/{TRELLO_BOARD_ID}/cards/all", {"fields": "name,labels,url,closed"})}

    entries, problems = [], []
    for a in get_board_comments_since(start):
        parsed = parse_tempo_line(a["data"].get("text", ""))
        if not parsed:
            continue
        work_date, summary, mins = parsed
        if not (start <= work_date <= end):
            continue
        cref = a["data"].get("card", {})
        card = cards.get(cref.get("id"), {"name": cref.get("name", "?"), "labels": [],
                                          "url": f"https://trello.com/c/{cref.get('shortLink', '')}"})
        tag, title = parse_tag(card["name"])
        label, billable = card_label(card)
        entry = {"date": work_date, "tag": tag.upper(), "title": title, "url": card.get("url", ""),
                 "label": label, "billable": billable, "summary": summary, "minutes": mins or 0}
        if mins is None:
            problems.append({**entry, "why": "has [date:] but no '- N minutes' on line 1"})
            continue
        entries.append(entry)
        if label == "unlabeled":
            problems.append({**entry, "why": "card has no billing label"})
    entries.sort(key=lambda e: (e["date"], e["tag"], e["title"].lower()))
    return entries, problems, windows


def fmt_mins(minutes: int) -> str:
    h, m = divmod(int(minutes), 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


def fmt_hours(minutes: int) -> str:
    return f"{minutes / 60:.2f}"


def build_morning_html(entries: list, problems: list, windows: dict, today: date) -> str:
    def esc(t) -> str:
        return (str(t).replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))

    def in_win(e, key):
        s, t = windows[key]
        return s <= e["date"] <= t

    def day(d: date) -> str:
        return d.strftime("%a %m/%d")

    def span(key) -> str:
        s, t = windows[key]
        return day(s) if s == t else f"{day(s)} to {day(t)}"

    keys = ["yesterday", "week", "month"]
    tot = {k: sum(e["minutes"] for e in entries if in_win(e, k)) for k in keys}
    bill = {k: sum(e["minutes"] for e in entries if in_win(e, k) and e["billable"]) for k in keys}

    th = "style='background:#1a1a2e;color:#fff;padding:6px 10px;text-align:left;font-size:12px'"
    thr = "style='background:#1a1a2e;color:#fff;padding:6px 10px;text-align:right;font-size:12px'"
    td = "style='padding:6px 10px;border-bottom:1px solid #eee;font-size:13px;vertical-align:top'"
    tdr = "style='padding:6px 10px;border-bottom:1px solid #eee;font-size:13px;text-align:right;white-space:nowrap'"
    tdb = "style='padding:6px 10px;font-size:13px;font-weight:bold;background:#f4f6f9'"
    tdbr = "style='padding:6px 10px;font-size:13px;font-weight:bold;background:#f4f6f9;text-align:right;white-space:nowrap'"
    table = "style='border-collapse:collapse;width:100%;max-width:900px;margin:6px 0 22px 0'"
    h2 = "style='font-size:16px;margin:18px 0 4px 0;color:#1a1a2e'"

    # KPI table
    kpi = "".join(
        f"<tr><td {td}><b>{label}</b><br><span style='color:#6b7280;font-size:12px'>{span(k)}</span></td>"
        f"<td {tdr}><b>{fmt_mins(tot[k])}</b></td><td {tdr}>{fmt_mins(bill[k])}</td>"
        f"<td {tdr}>{fmt_mins(tot[k] - bill[k])}</td></tr>"
        for k, label in [("yesterday", "Yesterday"), ("week", "Week to date"), ("month", "Month to date")])

    # Yesterday's entries
    multi_day = windows["yesterday"][0] != windows["yesterday"][1]
    y_rows = ""
    for e in (e for e in entries if in_win(e, "yesterday")):
        date_cell = f"<td {td}>{day(e['date'])}</td>" if multi_day else ""
        color = "#065f46" if e["billable"] else "#6b7280"
        y_rows += (f"<tr>{date_cell}<td {td}><b>{esc(e['tag'])}</b></td>"
                   f"<td {td}><a href='{esc(e['url'])}' style='color:#0066cc'>{esc(e['title'])}</a>"
                   f"<br><span style='color:#6b7280;font-size:12px'>{esc(e['summary'])}</span></td>"
                   f"<td {td}><span style='color:{color}'>{esc(e['label'])}</span></td>"
                   f"<td {tdr}>{e['minutes']}</td></tr>")
    if not y_rows:
        y_rows = f"<tr><td {td} colspan='{5 if multi_day else 4}'>No Tempo entries.</td></tr>"
    y_rows += (f"<tr><td {tdb} colspan='{4 if multi_day else 3}'>Total</td>"
               f"<td {tdbr}>{tot['yesterday']} ({fmt_mins(tot['yesterday'])})</td></tr>")
    y_head = (f"<tr>{f'<th {th}>Date</th>' if multi_day else ''}<th {th}>Client</th><th {th}>Card</th>"
              f"<th {th}>Label</th><th {thr}>Minutes</th></tr>")

    # Totals grids (hours) by client and by label
    def grid(group_key, order=None):
        groups = {}
        for e in entries:
            groups.setdefault(e[group_key], {k: 0 for k in keys})
            for k in keys:
                if in_win(e, k):
                    groups[e[group_key]][k] += e["minutes"]
        names = [n for n in order if n in groups] if order else sorted(groups)
        rows = "".join(f"<tr><td {td}>{esc(n)}</td>" + "".join(
            f"<td {tdr}>{fmt_hours(groups[n][k]) if groups[n][k] else ''}</td>" for k in keys) + "</tr>"
            for n in names)
        rows += f"<tr><td {tdb}>Total</td>" + "".join(f"<td {tdbr}>{fmt_hours(tot[k])}</td>" for k in keys) + "</tr>"
        return (f"<table {table}><tr><th {th}>{'Client' if group_key == 'tag' else 'Label'}</th>"
                f"<th {thr}>Yesterday</th><th {thr}>Week to date</th><th {thr}>Month to date</th></tr>{rows}</table>")

    bvn = (f"<table {table}><tr><th {th}></th><th {thr}>Yesterday</th><th {thr}>Week to date</th>"
           f"<th {thr}>Month to date</th></tr>"
           f"<tr><td {td}><b>Billable</b></td>" + "".join(f"<td {tdr}>{fmt_hours(bill[k])}</td>" for k in keys) + "</tr>"
           f"<tr><td {td}><b>Non-billable</b></td>" + "".join(f"<td {tdr}>{fmt_hours(tot[k] - bill[k])}</td>" for k in keys) + "</tr>"
           f"<tr><td {tdb}>Total</td>" + "".join(f"<td {tdbr}>{fmt_hours(tot[k])}</td>" for k in keys) + "</tr></table>")

    prob_html = ""
    if problems:
        items = "".join(f"<li>{day(p['date'])} <b>{esc(p['tag'])}</b> "
                        f"<a href='{esc(p['url'])}' style='color:#0066cc'>{esc(p['title'])}</a>: {esc(p['why'])}</li>"
                        for p in problems)
        prob_html = (f"<h2 {h2}>Needs a look</h2><ul style='font-size:13px;color:#92400e'>{items}</ul>")

    generated = datetime.now(CENTRAL_TZ).strftime("%Y-%m-%d %I:%M %p")
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>TIMEBOT morning {today}</title></head>
<body style="font-family:-apple-system,'Segoe UI',Arial,sans-serif;color:#1a1a2e;background:#ffffff;margin:0;padding:16px">
<h1 style="font-size:20px;margin:0">TIMEBOT morning report: {day(windows['yesterday'][1]) if not multi_day else span('yesterday')}</h1>
<p style="color:#6b7280;font-size:12px;margin:4px 0 12px 0">Generated {generated} Central. Counts Tempo comments by their [date:] work date (Central).</p>
<table {table}><tr><th {th}></th><th {thr}>Total</th><th {thr}>Billable</th><th {thr}>Non-billable</th></tr>{kpi}</table>
<h2 {h2}>Yesterday: {span('yesterday')}</h2>
<table {table}>{y_head}{y_rows}</table>
<h2 {h2}>Billable vs non-billable (hours)</h2>{bvn}
<h2 {h2}>By client (hours)</h2>{grid('tag')}
<h2 {h2}>By label (hours)</h2>{grid('label', LABEL_ORDER)}
{prob_html}
<p style="color:#9ca3af;font-size:11px">Billable = project, support-billable, change-order, retainer. Week starts Monday. Generated by TIMEBOT.</p>
</body></html>"""


def run_morning(today: date, send: bool = True) -> dict:
    entries, problems, windows = collect_tempo(today)
    y_start, y_end = windows["yesterday"]
    y_mins = sum(e["minutes"] for e in entries if y_start <= e["date"] <= y_end)
    wk_mins = sum(e["minutes"] for e in entries if windows["week"][0] <= e["date"] <= y_end)
    mo_mins = sum(e["minutes"] for e in entries if windows["month"][0] <= e["date"] <= y_end)
    label = y_end.strftime("%a %m/%d") if y_start == y_end else f"{y_start:%a %m/%d} to {y_end:%a %m/%d}"
    subject = (f"TIMEBOT morning | {label} | {fmt_mins(y_mins)} | "
               f"WTD {fmt_mins(wk_mins)} | MTD {fmt_mins(mo_mins)}")
    html = build_morning_html(entries, problems, windows, today)
    if send:
        send_email(subject, html)
    log.info("Morning done: %s", subject)
    return {"statusCode": 200, "subject": subject, "entries": len(entries),
            "problems": len(problems), "html": None if send else html}


# ── Handler ────────────────────────────────────────────────────────────────────
def lambda_handler(event, context):
    if (event or {}).get("report") == "morning":
        today = date.fromisoformat(event["today"]) if event.get("today") else datetime.now(CENTRAL_TZ).date()
        result = run_morning(today)
        result.pop("html", None)
        return result

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


# ── Local dry run: python lambda_function.py --morning 2026-11-05 out.html ─────
if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 4 and sys.argv[1] == "--morning":
        res = run_morning(date.fromisoformat(sys.argv[2]), send=False)
        with open(sys.argv[3], "w", encoding="utf-8") as f:
            f.write(res.pop("html"))
        print(res)
