# Timebot

Automated daily work reporter for Savvy Otter. Reads the [Savvy Otter Items](https://trello.com/b/mdS3ny24/savvy-otter-items) Trello board each day at **5:00 PM Central**, finds cards that were created or updated that day, extracts time entries from card comments, and generates two reports per day.

---

## Reports

Each run produces two files in `daily_reports/`:

| File | Description |
|---|---|
| `YYYY-MM-DD.md` | Verbose markdown report grouped by client/project tag |
| `YYYY-MM-DD.csv` | Flat CSV for spreadsheet import or tracking |

> Note: `daily_reports/` is in `.gitignore` — reports are local only.

---

## Card Format

Cards on the Trello board should be named with a `[TAG]` prefix to identify the client or project:

```
[Acme Corp] Fix login bug
[Internal] Update deployment pipeline
[Project Phoenix] Design review
```

Cards without a tag are grouped under **Uncategorized**.

---

## Time Logging

Timebot scans **card comments** posted today for time entries. Supported formats (case-insensitive):

| You write | Interpreted as |
|---|---|
| `2h` / `2hr` / `2 hours` | 2 hours |
| `30m` / `30min` / `30 minutes` | 30 minutes |
| `1.5h` / `1.5 hours` | 1 hour 30 minutes |
| `1:30` | 1 hour 30 minutes |
| `2h 30m` | 2 hours 30 minutes |

Time entries can appear anywhere in a comment alongside other text, e.g.:
> "Finished the API integration. 1.5h spent on debugging the auth flow."

---

## Setup

### Prerequisites
- Python 3.11+
- A Trello account with API access

### Install

```bash
git clone https://github.com/Savvyotter/timebot.git
cd timebot
pip install -r requirements.txt
cp .env.example .env
# Edit .env and fill in your credentials
```

### Environment Variables

```env
TRELLO_API_KEY=your_trello_api_key
TRELLO_API_TOKEN=your_trello_api_token
TRELLO_BOARD_ID=mdS3ny24
```

Get your API key and token at: https://trello.com/app-key

---

## Running Manually

```bash
python timebot.py
```

Output will be written to `daily_reports/YYYY-MM-DD.md` and `daily_reports/YYYY-MM-DD.csv`.

---

## Automated Schedule

Timebot is scheduled to run daily at **5:00 PM Central Time** via Claude Code's built-in cron scheduler. The job fires automatically when the Claude Code session is active.

---

## Report Structure

### Markdown Report (`YYYY-MM-DD.md`)

```
# Daily Work Report — 2026-03-16

> Generated: 2026-03-16 05:00 PM Central
> Cards updated today: 4
> Total logged time: 5h 30m

---

## [Acme Corp]
*2 card(s) · 3h*

### Fix login bug
- Status: Done
- Time Logged: 1h 30m
- Card URL: https://trello.com/...
- Comments today:
  > Fixed the auth token expiry issue. 1.5h

...

## Summary Table
| Tag / Client | Card | Status | Time Logged |
|---|---|---|---|
| Acme Corp | Fix login bug | Done | 1h 30m |
...
```

### CSV Report (`YYYY-MM-DD.csv`)

```csv
Date,Tag/Client,Card Title,Status/List,Time Logged,Activity,URL
2026-03-16,Acme Corp,Fix login bug,Done,1h 30m,,https://trello.com/...
```

---

## Project Structure

```
timebot/
├── timebot.py          # Main script
├── requirements.txt    # Python dependencies
├── .env.example        # Environment variable template
├── .gitignore
├── README.md
└── daily_reports/      # Generated reports (gitignored)
    ├── 2026-03-16.md
    └── 2026-03-16.csv
```

---

*Maintained by [Savvy Otter](https://github.com/Savvyotter)*
