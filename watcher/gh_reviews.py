"""GitHub PRs waiting on Nat's review -> Dev Ops cards in "Deploy To Staging Request".

Usage: python gh_reviews.py [--dry-run]
Runs `gh search prs --review-requested=Nat-Thompson --state=open --owner SavvyOtterInc` (gh's own
stored login). For each PR:
  - already on a Dev Ops card (its URL is in any card desc, archived cards included): nothing to do
  - dependabot or another bot: event pr_review_bot (listed in the report, never carded unless Nat asks)
  - draft: event pr_review_draft (not ready for review, no card)
  - repo seen before on Dev Ops cards: create the card "[Client] - <title> (PR #N)" (client prefix =
    the one most used on that repo's earlier cards), verify it, event pr_review_carded
  - unknown repo: event pr_review_uncarded, so the Claude tier or WATCHER picks the client and cards it
devops_sync.py then mirrors the new card onto Items, and the pass hands it to OTTER-OPS as usual.
"""
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

from _trello import DEVOPS, DEVOPS_LISTS, DRY, DryRun, STATE, call, get, save_json
from dashboard import scrub
from journal import add

CT = ZoneInfo("America/Chicago")
SEEN = os.path.join(STATE, "gh_prs_seen.json")
BY = os.environ.get("WATCHER_HOLDER", "interactive")
RECORD = not DRY or os.environ.get("WATCHER_SHADOW") == "1"
PR_URL = re.compile(r"https://github\.com/SavvyOtterInc/([A-Za-z0-9_.-]+)/pull/(\d+)")


def gh(*args):
    r = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8", timeout=90)
    if r.returncode:
        sys.exit(f"gh failed ({r.returncode}): {r.stderr.strip()[:300]}")
    return json.loads(r.stdout)


def first_paragraph(body):
    text = re.sub(r"<!--.*?-->", "", body or "", flags=re.S)
    for para in re.split(r"\n\s*\n", text):
        para = " ".join(l.strip() for l in para.splitlines() if l.strip() and not l.lstrip().startswith("#"))
        if len(para) > 20:
            return scrub(para)[:500]
    return "No description on the PR."


def record(kind, ref, detail):
    print(kind.upper(), ref, "|", detail)
    if RECORD:
        add("events", kind, ref, detail, by=BY)
        if kind != "pr_review_carded" or DRY:
            seen.setdefault(ref, {})["reported"] = kind


prs = gh("search", "prs", "--review-requested=Nat-Thompson", "--state=open", "--owner", "SavvyOtterInc",
         "--json", "url,title,number,repository,author,createdAt,isDraft", "--limit", "50")
cards = get(f"boards/{DEVOPS}/cards/all?fields=name,desc,shortLink,shortUrl")
on_card = {}
prefixes = {}
for c in cards:
    for m in PR_URL.finditer(c.get("desc") or ""):
        on_card.setdefault(m.group(0), c["shortLink"])
        pre = re.match(r"\s*\[([^\]]+)\]", c["name"])
        if pre:
            prefixes.setdefault(m.group(1), Counter())[pre.group(1).strip()] += 1
seen = json.load(open(SEEN, encoding="utf-8")) if os.path.exists(SEEN) else {}

for pr in prs:
    url, repo, num = pr["url"], pr["repository"]["name"], pr["number"]
    author = pr["author"].get("login", "?")
    opened = datetime.fromisoformat(pr["createdAt"].replace("Z", "+00:00")).astimezone(CT).strftime("%Y-%m-%d %H:%M CT")
    if url in on_card:
        print("ON CARD", url, "->", on_card[url])
        seen.setdefault(url, {})["card"] = on_card[url]
        continue
    if seen.get(url, {}).get("reported"):  # bot, draft, unknown-repo or dry-run PRs: report once, not every pass
        print("ALREADY REPORTED", url, "|", seen[url]["reported"])
        continue
    if pr["author"].get("is_bot") or author.endswith("[bot]") or author.startswith("app/"):
        record("pr_review_bot", url, f"{repo} #{num} by {author} | {pr['title'][:120]}")
        continue
    if pr.get("isDraft"):
        record("pr_review_draft", url, f"{repo} #{num} by {author} | {pr['title'][:120]}")
        continue
    if repo not in prefixes:
        record("pr_review_uncarded", url, f"{repo} #{num} by {author}, opened {opened} | {pr['title'][:120]} | unknown client for repo")
        continue
    client = prefixes[repo].most_common(1)[0][0]
    view = gh("pr", "view", url, "--json", "body,baseRefName")
    name = f"[{client}] - {pr['title'].strip()} (PR #{num})"
    desc = (f"{url}\n\nAuthor: {author}\nBase branch: {view.get('baseRefName', '?')}\nOpened: {opened}\n\n"
            f"Why: {first_paragraph(view.get('body'))}\n\n"
            "Card created by WATCHER (background) from a GitHub review request for Nat.")
    try:
        made = call("POST", "cards", {"idList": DEVOPS_LISTS["devops-staging-request"], "name": name,
                                      "desc": desc, "pos": "top"})
    except DryRun:
        record("pr_review_carded", url, f"(dry run, no card) {name}")
        continue
    assert made["name"] == name and made["desc"] == desc, "stored card differs"
    seen[url] = {"card": made["shortLink"], "created": opened}
    record("pr_review_carded", url, f"dev ops {made['shortLink']} | {name}")

if RECORD:  # real runs, and dry runs against the runner's shadow state
    save_json(SEEN, seen)
print("review requests:", len(prs))
