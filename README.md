# TIMEBOT

Automated daily work reporter for Savvy Otter. An AWS Lambda function triggered every day at **5:00 PM Central** via EventBridge Scheduler. Reads the [Savvy Otter Items](https://trello.com/b/mdS3ny24/savvy-otter-items) Trello board, finds cards created or updated that day, extracts time entries from comments, and emails a styled HTML report via SES.

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
| Lambda function | `timebot` (us-east-1) |
| Lambda exec role | `lambda_exec_timebot` |
| EventBridge schedule | `timebot-daily-5pm` |
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

```bash
# Install dependencies into build/
pip install requests tzdata -t build/
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
  --log-type Tail \
  response.json
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
