# TIMEBOT

Automated daily work reporter for Savvy Otter. An AWS Lambda function that reads the [Savvy Otter Items](https://trello.com/b/mdS3ny24/savvy-otter-items) Trello board and emails an HTML Tempo report via SES.

> **Current state (2026-11-05):** only the **7:30 AM morning report** is live. The original 5 PM report (Lambda + `timebot-daily-5pm`) was decommissioned in March 2026; on 2026-11-05 the `timebot` function was recreated for the morning report only. The 5 PM code path is still in `lambda_function.py` (default when the event has no `report` key) but nothing schedules it.

---

## Morning Report (7:30 AM Central)

EventBridge schedule `timebot-daily-730am`, `cron(30 7 * * ? *)`, timezone `America/Chicago`, sends the event `{"report": "morning"}`. Optional `"today": "YYYY-MM-DD"` overrides the run date for a re-send.

**Subject:** `TIMEBOT morning | Wed 11/04 | 2h 16m | WTD 11h 41m | MTD 11h 41m`

**What it counts:** Tempo comments in Nat's standard format, one per card per work day, edited in place. Only line 1 is read:

```
[date: YYYY-MM-DD] <summary> - <N> minutes
```

- The `[date:]` value is the **work date** and is what the report keys on (not the comment's post date).
- The last `- N minutes` on line 1 wins, so trailing notes like `(topped up from 44 ...)` are fine.
- Comments without a `[date:]` first line (watcher notes, "Merged into..." notes) are not Tempo and are ignored.
- A `[date:]` line with no minutes, or a card with no billing label, is listed under **Needs a look**.

**Windows (Central dates, inclusive):**

| Section | Normal day | Monday |
|---|---|---|
| Yesterday | yesterday | Fri, Sat and Sun |
| Week to date | Monday through yesterday | previous Mon through Sun |
| Month to date | 1st of yesterday's month through yesterday | same |

**Labels** (card's first matching Trello label):

| Trello label | Report label | Billable |
|---|---|---|
| `Project` | project | yes |
| `Support - Billable`, legacy `Support - Warranty` | support-billable | yes |
| `Change Order` | change-order | yes |
| `Retainer` | retainer | yes |
| `Support - Non Billable`, legacy `Support - Non Warranty` | support-nonbillable | no |
| `Operations` | operations | no |
| none | unlabeled | no (flagged) |

The email has: a total / billable / non-billable summary for all three windows, yesterday's entries (client, card, summary, label, minutes), and hours grids by billable vs non-billable, by client and by label.

**Local dry run** (no email; needs `.env`):

```bash
set -a; . ./.env; set +a
python lambda_function.py --morning 2026-11-05 morning_reports/2026-11-04_morning.html
```

---

## Architecture

```
EventBridge Scheduler (cron 0 17 * * ? *, America/Chicago)
    └─▶ Lambda: timebot (python3.12)
            ├─▶ Trello API  — fetch updated cards + actions
            └─▶ AWS SES     — send HTML email report
```

**AWS Resources:**
| Resource | Name / ARN |
|---|---|
| Lambda function | `timebot` (us-east-1), recreated 2026-11-05, published version 1 |
| Lambda exec role | `lambda_exec_timebot` |
| EventBridge schedule | `timebot-daily-730am` (live), `timebot-daily-5pm` (deleted March 2026) |
| Scheduler role | `scheduler_exec_timebot` |
| SES sending domain | `savvyottermations.com` (verified) |

---

## Card Format

Cards should be named with a `[TAG]` prefix identifying the client or project:

```
[Acme Corp] Fix login bug
[SAVVYOTTER] Internal planning meeting
[HEARTLAND] Address server offline
```

Cards without a tag are grouped under **Uncategorized**.

---

## Time Logging

TIMEBOT scans **card comments** posted today for explicit duration entries. Supported formats (case-insensitive):

| You write | Interpreted as |
|---|---|
| `2h` / `2hr` / `2 hours` | 2 hours |
| `30m` / `30 min` / `30 minutes` | 30 minutes |
| `1.5h` / `1.5 hours` | 1 hour 30 minutes |
| `.5 hours` / `.25 hours` | 30 min / 15 min |

Time entries can appear anywhere in the comment alongside timestamps:

> `9:00 - 9:15 am — Set up email report — 0.25 hours`
> `5 am to 8 am - Deep work on API — 3 hours`

Clock times (like `9:00`, `10:15 am`) are intentionally ignored — only explicit durations (`0.25 hours`, `3 hours`) are counted.

---

## Email Report

**Subject:** `TIMEBOT | 2026-03-18 | 5 cards | 4h 50m`

The HTML body includes:
- KPI header (card count, total time, client count)
- Per-client sections with card details, status movements, and comment text
- Summary table with total row

---

## Lambda Environment Variables

| Variable | Default | Description |
|---|---|---|
| `TRELLO_API_KEY` | *(required)* | Trello API key |
| `TRELLO_API_TOKEN` | *(required)* | Trello API token |
| `TRELLO_BOARD_ID` | `mdS3ny24` | Board short ID |
| `SES_FROM` | `TIMEBOT <noreply@savvyottermations.com>` | Sender address |
| `SES_TO` | `nat.thompson@savvyotter.com` | Recipient address |
| `SES_REGION` | `us-east-1` | AWS region for SES |

---

## Deployment

### Prerequisites
- Python 3.11+
- AWS CLI configured with `savvyotter` profile
- `savvyottermations.com` verified in SES

### Package & Deploy

Use Linux wheels (a plain `pip install` on Windows pulls Windows-only binaries such as charset_normalizer's mypyc `.pyd`). The AWS CLI here is v1, so `aws lambda invoke` needs no `--cli-binary-format`.

```bash
# Install dependencies into build/
pip install requests tzdata -t build/ --platform manylinux2014_x86_64 --only-binary=:all: --python-version 3.12 --implementation cp
cp lambda_function.py build/

# Zip (Windows)
cd build && powershell Compress-Archive -Path * -DestinationPath ../timebot.zip -Force && cd ..

# Update Lambda
aws lambda update-function-code \
  --profile savvyotter \
  --region us-east-1 \
  --function-name timebot \
  --zip-file fileb://timebot.zip
```

### Test

```bash
aws lambda invoke \
  --profile savvyotter \
  --region us-east-1 \
  --function-name timebot \
  --payload '{"report":"morning"}' \
  --log-type Tail \
  response.json
```

### Rollback

`aws lambda update-function-code` with the previous zip, or point back to a published version. Version 1 (2026-11-05, CodeSha256 `alNFU169VIXuwpjF4NcB6GspsyH1Nv/46JYgxX2AGVc=`) is the first morning-report build. To stop the email: `aws scheduler update-schedule ... --state DISABLED` on `timebot-daily-730am` (repeat its full target) or delete the schedule.
```

---

## Project Structure

```
timebot/
├── lambda_function.py   # Lambda handler — all logic
├── requirements.txt     # Python dependencies
├── .env.example         # Local dev env template
├── .gitignore
└── README.md
```

---

*Maintained by [Savvy Otter](https://github.com/Nat-Thompson)*
