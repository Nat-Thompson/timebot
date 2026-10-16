"""Background WATCHER pass, run by Windows Task Scheduler as Nat (see bg/WATCHER-bg.xml).

Usage: pythonw bg/runner.py                      # a live pass
       python  bg/runner.py --dry-run            # writes nothing outside a shadow copy of state
       python  bg/runner.py --dry-run --state-from C:/git/savvyotter/timebot/watcher/state
       options: --force-claude  --no-claude  --model sonnet

One pass:
  1. Take state/watcher.lock (holder "bg"). If the interactive WATCHER holds it, log and exit.
  2. Sync ALEXANDRIA (pull --ff-only).
  3. Tier A, plain Python: doing_diff, devops_sync, gh_reviews, dashboard. Their changes land in
     state/events.jsonl.
  4. Tier B, `claude -p` with the watcher-bg-pass skill, only when something is open or a poll is due
     (about every 25 min on workdays 7 AM-7 PM CT, 55 min otherwise), and only under the usage caps.
     Locked down three ways: --disallowedTools removes dangerous tools, permission rules +
     dontAsk + --permission-prompts none deny anything unlisted, and the bg/guard.py PreToolUse hook
     re-checks every call.
  5. Push new P1 alerts (toast + BARK Slack), write state/runner_status.json, release the lock.
Dry run: state is copied to state/dryrun/<stamp>/ and every script and the guard run with
WATCHER_DRY_RUN=1, so Trello, Gmail, Drive, sessions, Slack and the dashboard are untouched.
Logs: <state>/logs/runner-YYYY-MM-DD.log, scrubbed, kept 14 days. Never logs env values.
"""
import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

BG = os.path.dirname(os.path.abspath(__file__))
WATCHER = os.path.dirname(BG)
REPO = os.path.dirname(WATCHER)
SKILL = os.path.join(REPO, ".claude", "skills", "watcher-bg-pass", "SKILL.md")
CT = ZoneInfo("America/Chicago")
NO_WINDOW = 0x08000000
CLAUDE = shutil.which("claude") or os.path.join(os.path.expanduser("~"), ".local", "bin", "claude.exe")

ALLOW_MCP = ["mcp__claude_ai_Gmail__search_threads", "mcp__claude_ai_Gmail__get_thread",
             "mcp__claude_ai_Gmail__get_message", "mcp__claude_ai_Gmail__list_labels",
             "mcp__claude_ai_Google_Calendar__list_events", "mcp__claude_ai_Google_Calendar__get_event",
             "mcp__claude_ai_Google_Calendar__list_calendars", "mcp__claude_ai_Google_Drive__create_file",
             "mcp__claude_ai_Google_Drive__read_file_content", "mcp__claude_ai_Google_Drive__search_files",
             "mcp__claude_ai_Google_Drive__get_file_metadata"]
SCRIPTS = ["find_card", "create_card", "add_label", "post_comment", "journal", "dashboard", "render_msg", "seen"]
ALLOW = (["Read", "Glob", "Grep", "Write", "Edit", "ToolSearch", "Skill", "ListAgents", "SendMessage", "ArtifactData",
          "Bash(python ../trello_read.py *)"] + [f"Bash(python {s}.py *)" for s in SCRIPTS] +
         [f"Bash(python {s}.py)" for s in ("dashboard",)] + ALLOW_MCP)
DENY = ([f"mcp__claude_ai_Gmail__{t}" for t in (
            "send_message", "reply", "forward", "create_draft", "update_draft", "delete_draft", "get_draft",
            "list_drafts", "trash_message", "trash_thread", "untrash_message", "untrash_thread", "mark_message_spam",
            "mark_thread_spam", "unmark_message_spam", "unmark_thread_spam", "label_message", "label_thread",
            "unlabel_message", "unlabel_thread", "update_message_labels", "create_label", "delete_label",
            "update_label", "apply_sensitive_message_label", "apply_sensitive_thread_label")] +
        [f"mcp__claude_ai_Google_Calendar__{t}" for t in (
            "create_event", "update_event", "delete_event", "respond_to_event", "suggest_time", "search_events")] +
        [f"mcp__claude_ai_Google_Drive__{t}" for t in (
            "share_file", "trash_file", "update_file", "copy_file", "download_file_content", "get_file_permissions",
            "list_recent_files")] +
        ["mcp__claude_ai_Google_Docs__update_doc", "mcp__claude_ai_Google_Docs__read_doc",
         "mcp__claude_ai_Claude_Docs__batch", "mcp__claude_ai_Claude_Docs__create", "mcp__claude_ai_Claude_Docs__update",
         "mcp__claude_ai_Claude_Docs__delete", "mcp__claude_ai_Claude_Docs__export", "mcp__claude_ai_Claude_Docs__query",
         "mcp__claude_ai_Claude_Docs__read", "mcp__claude_ai_Claude_Docs__guide",
         "WebFetch", "WebSearch", "PowerShell", "Agent", "Task", "Artifact", "ArtifactComments", "NotebookEdit",
         "CronCreate", "CronDelete", "Workflow", "RemoteTrigger", "PushNotification", "EnterWorktree"])

PER_RUN_USD = 3.0     # API-equivalent estimate per Claude run (subscription, not billed); --max-budget-usd
DAILY_USD = 40.0      # stop Tier B for the day past this; override with WATCHER_BG_DAILY_USD in .env
STEP_TIMEOUT = 180
CLAUDE_TIMEOUT = 12 * 60


def now_utc():
    return datetime.now(timezone.utc)


class Pass:
    def __init__(self, args):
        self.args = args
        self.dry = args.dry_run
        self.t0 = now_utc()
        self.ct = self.t0.astimezone(CT)
        real_state = os.path.abspath(args.state_from or os.path.join(WATCHER, "state"))
        if self.dry:
            self.state = os.path.join(WATCHER, "state", "dryrun", self.ct.strftime("%Y%m%d-%H%M%S"))
            os.makedirs(self.state)
            for f in os.listdir(real_state):
                src = os.path.join(real_state, f)
                if os.path.isfile(src) and f != "watcher.lock" and os.path.splitext(f)[1] in (".json", ".txt", ".md", ".jsonl"):
                    shutil.copy2(src, self.state)
        else:
            self.state = real_state
        os.makedirs(os.path.join(self.state, "logs"), exist_ok=True)
        self.logf = os.path.join(self.state, "logs", f"runner-{self.ct:%Y-%m-%d}.log")
        self.env_file = os.environ.get("TIMEBOT_ENV") or os.path.join(REPO, ".env")
        self.env = {**os.environ, "WATCHER_DIR": WATCHER, "WATCHER_STATE": self.state, "WATCHER_HOLDER": "bg",
                    "TIMEBOT_ENV": self.env_file, "ALEXANDRIA_DIR": r"C:\minions\SavvyAI\ALEXANDRIA",
                    "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        if self.dry:
            self.env.update({"WATCHER_DRY_RUN": "1", "WATCHER_SHADOW": "1"})
        else:
            self.env.pop("WATCHER_DRY_RUN", None)
            self.env.pop("WATCHER_SHADOW", None)
        if self.env.pop("ANTHROPIC_API_KEY", None):
            self.log("NOTE ANTHROPIC_API_KEY was set in the environment; removed so the run uses the subscription login")
        self.status = {"started_ct": self.ct.strftime("%Y-%m-%d %H:%M:%S CT"), "dry_run": self.dry, "steps": {},
                       "claude": None, "errors": []}
        sys.path.insert(0, WATCHER)
        os.environ.update({"WATCHER_STATE": self.state, "TIMEBOT_ENV": self.env_file})
        from dashboard import scrub  # noqa: E402  (needs the env above)
        self.scrub = scrub

    # ---------- helpers ----------
    def log(self, msg):
        line = f"{datetime.now(CT):%H:%M:%S} {msg}"
        try:
            with open(self.logf, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except (OSError, AttributeError):
            pass
        if sys.stdout:
            try:
                print(line, flush=True)
            except OSError:
                pass

    def path(self, name):
        return os.path.join(self.state, name)

    def read_json(self, name, default):
        try:
            with open(self.path(name), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return default

    def write_json(self, name, data):
        tmp = self.path(name) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path(name))

    def run(self, argv, timeout=STEP_TIMEOUT, **kw):
        return subprocess.run(argv, cwd=WATCHER, env=self.env, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, creationflags=NO_WINDOW, **kw)

    # ---------- steps ----------
    def lock(self):
        sys.path.insert(0, WATCHER)
        import lock
        lock.LOCK = self.path("watcher.lock")
        self.lockmod = lock
        ok, info = lock.acquire("bg")
        if not ok:
            self.log(f"SKIP lock held by {info.get('holder') if info else '?'} since {info.get('started') if info else '?'}")
            return False
        stop = self.hb_stop = threading.Event()

        def beat():
            while not stop.wait(60):
                lock.heartbeat("bg")
        threading.Thread(target=beat, daemon=True).start()
        return True

    def alexandria(self):
        try:
            r = subprocess.run(["git", "-C", r"C:\minions\SavvyAI\ALEXANDRIA", "pull", "--ff-only", "-q", "origin", "main"],
                               capture_output=True, text=True, timeout=90, creationflags=NO_WINDOW)
            self.log("ALEXANDRIA pull " + ("ok" if r.returncode == 0 else f"FAILED {r.stderr.strip()[:200]}"))
        except (OSError, subprocess.TimeoutExpired) as e:
            self.log(f"ALEXANDRIA pull FAILED {type(e).__name__}")

    def tier_a(self):
        outputs = {}
        steps = [("doing_diff", ["doing_diff.py"]), ("devops_sync", ["devops_sync.py"] + (["--dry-run"] if self.dry else [])),
                 ("gh_reviews", ["gh_reviews.py"]), ("dashboard", ["dashboard.py"])]
        for name, argv in steps:
            t = time.time()
            try:
                r = self.run([sys.executable.replace("pythonw", "python")] + argv)
                out = self.scrub((r.stdout or "") + (("\nSTDERR: " + r.stderr[-600:]) if r.returncode else ""))
                ok = r.returncode == 0
            except subprocess.TimeoutExpired:
                out, ok = "TIMEOUT", False
            outputs[name] = "\n".join(out.strip().splitlines()[-60:])
            self.status["steps"][name] = "ok" if ok else "FAILED"
            if not ok:
                self.status["errors"].append(f"{name} failed")
            self.log(f"A {name} {'ok' if ok else 'FAILED'} {time.time() - t:.1f}s | " + " / ".join(outputs[name].splitlines()[-3:])[:400])
        return outputs

    def claude_due(self):
        """(due, reason, context flags)."""
        import journal
        journal.STATE = self.state
        events = [e for e in journal.open_entries("events")]
        last = self.read_json("bg_last.json", {})
        last_claude = datetime.fromisoformat(last["claude"]) if last.get("claude") else None
        workday = self.ct.weekday() < 5 and 7 <= self.ct.hour < 19
        interval = 25 if workday else 55
        first_today = not last_claude or last_claude.astimezone(CT).date() != self.ct.date()
        mins = (self.t0 - last_claude).total_seconds() / 60 if last_claude else 10 ** 6
        flags = {"first_pass_today": first_today, "workday_hours": workday, "events": events,
                 "since": (last_claude - timedelta(minutes=5)) if last_claude else self.t0 - timedelta(hours=2),
                 "calendar_due": first_today or mins >= 55}
        reviews = last.get("reviews", {})
        today = self.ct.date().isoformat()
        if 8 <= self.ct.hour < 12 and reviews.get("morning") != today:
            flags["review"] = "morning"
        elif 16 <= self.ct.hour < 17 and reviews.get("afternoon") != today:
            flags["review"] = "afternoon"
        if self.args.no_claude:
            return False, "--no-claude", flags
        if self.args.force_claude:
            return True, "--force-claude", flags
        if events:
            return True, f"{len(events)} open events", flags
        if mins >= interval or first_today:
            return True, f"poll due ({'first today' if first_today else f'{mins:.0f} min since last'})", flags
        return False, f"nothing open, last Claude pass {mins:.0f} min ago", flags

    def over_budget(self):
        try:
            from _trello import load_env
            cap = float(load_env().get("WATCHER_BG_DAILY_USD", DAILY_USD))
        except (OSError, ValueError, KeyError):
            cap = DAILY_USD
        used = self.read_json("usage.json", {}).get(self.ct.date().isoformat(), {}).get("usd", 0.0)
        return used >= cap, used, cap

    def settings(self):
        guard = os.path.join(BG, "guard.py")
        py = sys.executable.replace("pythonw", "python")
        s = {"permissions": {"allow": ALLOW, "deny": DENY, "disableBypassPermissionsMode": "disable"},
             "hooks": {"PreToolUse": [{"matcher": "*", "hooks": [
                 {"type": "command", "command": f'"{py}" "{guard}"', "timeout": 15}]}]}}
        p = self.path("bg_settings.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=1)
        return p

    def prompt(self, flags, tier_a):
        ev = "\n".join(json.dumps({k: e.get(k) for k in ("id", "kind", "ref", "detail")}, ensure_ascii=False)
                       for e in flags["events"]) or "(none)"
        since = flags["since"]
        mode = "DRY RUN: make no external writes; write WOULD lines to dryrun_report.md." if self.dry else "LIVE pass."
        return f"""Run one background WATCHER pass per your watcher-bg-pass instructions. {mode}

Now: {self.ct:%Y-%m-%d %H:%M} CT ({self.t0:%Y-%m-%dT%H:%M:%SZ}).
Scan window: since {since.astimezone(CT):%Y-%m-%d %H:%M} CT, Gmail `after:{int(since.timestamp())}`.
State dir: {self.state}
First pass today: {'yes' if flags['first_pass_today'] else 'no'}. Calendar due: {'yes' if flags['calendar_due'] else 'no'}.
Needs-reply review: {flags.get('review', 'no')}.

Open events (journal.py events):
{ev}

Tier A output (already done by the runner):
--- doing_diff
{tier_a.get('doing_diff', '')}
--- devops_sync
{tier_a.get('devops_sync', '')}
--- gh_reviews
{tier_a.get('gh_reviews', '')}
--- dashboard
{tier_a.get('dashboard', '')}

Recent notes from earlier passes (bg_notes.md, last lines):
{self.tail('bg_notes.md', 12)}
"""

    def tail(self, name, n):
        try:
            with open(self.path(name), encoding="utf-8") as f:
                return "".join(f.readlines()[-n:]).strip() or "(none)"
        except OSError:
            return "(none)"

    def tier_b(self, flags, tier_a):
        over, used, cap = self.over_budget()
        if over:
            self.log(f"B SKIP daily usage cap reached ({used:.2f} of {cap:.2f} USD-equivalent)")
            self.status["claude"] = "skipped: daily cap"
            self.alert_once("cap", "Background WATCHER paused for today",
                            f"Claude usage estimate reached {used:.0f} of {cap:.0f} USD-equivalent. Python steps keep running.")
            return
        cmd = [CLAUDE, "-p", "--model", self.args.model, "--permission-mode", "dontAsk", "--permission-prompts", "none",
               "--settings", self.settings(), "--append-system-prompt-file", SKILL, "--max-turns", "80",
               "--max-budget-usd", str(PER_RUN_USD), "--output-format", "json",
               "--allowedTools", ",".join(ALLOW), "--disallowedTools", ",".join(DENY)]
        t = time.time()
        try:
            r = self.run(cmd, timeout=CLAUDE_TIMEOUT, input=self.prompt(flags, tier_a))
            lines = [l for l in (r.stdout or "").strip().splitlines() if l.startswith("{")]
            res = json.loads(lines[-1]) if lines else {"is_error": True, "result": (r.stderr or "no output")[-500:]}
        except subprocess.TimeoutExpired:
            res = {"is_error": True, "result": "TIMEOUT after 12 minutes", "subtype": "timeout"}
        except ValueError:
            res = {"is_error": True, "result": "unparseable output", "subtype": "bad_json"}
        cost = float(res.get("total_cost_usd") or 0)
        text = self.scrub(str(res.get("result") or ""))
        err = bool(res.get("is_error"))
        self.log(f"B claude {'ERROR' if err else 'ok'} {res.get('subtype')} turns={res.get('num_turns')} "
                 f"usd_equiv={cost:.3f} {time.time() - t:.0f}s denials={len(res.get('permission_denials') or [])}")
        self.log("B report: " + text[:3000].replace("\n", " / "))
        usage = self.read_json("usage.json", {})
        day = usage.setdefault(self.ct.date().isoformat(), {"usd": 0.0, "runs": 0})
        day["usd"] = round(day["usd"] + cost, 4)
        day["runs"] += 1
        for k in sorted(usage)[:-14]:
            usage.pop(k)
        self.write_json("usage.json", usage)
        self.status["claude"] = {"ok": not err, "subtype": res.get("subtype"), "usd_equiv": round(cost, 3),
                                 "turns": res.get("num_turns"), "seconds": round(time.time() - t)}
        if err:
            self.status["errors"].append("claude: " + text[:200])
            if "401" in text or "authenticate" in text.lower():
                self.alert_once("login", "Background WATCHER: Claude login expired",
                                "Run `claude /login` in a terminal. Python steps keep running until then.")
            return
        last = self.read_json("bg_last.json", {})
        last["claude"] = self.t0.isoformat()
        if flags.get("review"):
            last.setdefault("reviews", {})[flags["review"]] = self.ct.date().isoformat()
        self.write_json("bg_last.json", last)

    def alert_once(self, key, title, body):
        """Runner-level alerts (login, cap, repeated failures): at most one per key per day."""
        sent = self.read_json("bg_alerts_sent.json", {})
        today = self.ct.date().isoformat()
        if sent.get(key) == today:
            return
        sent[key] = today
        self.write_json("bg_alerts_sent.json", sent)
        self.push(title, body)

    def push(self, title, body):
        sys.path.insert(0, BG)
        import alert
        from _trello import load_env
        try:
            hook = load_env().get("SLACK_WEBHOOK_URL")
        except OSError:
            hook = None
        self.log(f"ALERT {title} | {body[:200]} | {alert.send(title, body, hook, dry=self.dry)}")

    def drain_alerts(self):
        import journal
        journal.STATE = self.state
        for a in journal.open_entries("alerts"):
            self.push(f"{a.get('kind', 'P1')} {a.get('ref', '')}".strip(), self.scrub(a.get("detail", ""))[:300])
            journal.mark("alerts", a["id"], "done", "bg-runner", "pushed")

    def failures(self):
        f = self.read_json("bg_failures.json", {"consecutive": 0})
        f["consecutive"] = f["consecutive"] + 1 if self.status["errors"] else 0
        self.write_json("bg_failures.json", f)
        if f["consecutive"] >= 3:
            self.alert_once("failures", "Background WATCHER is failing",
                            f"{f['consecutive']} passes in a row had errors. Last: {'; '.join(self.status['errors'])[:200]}")

    def prune_logs(self):
        cutoff = time.time() - 14 * 86400
        for p in glob.glob(os.path.join(self.state, "logs", "*.log")):
            if os.path.getmtime(p) < cutoff:
                os.remove(p)

    def go(self):
        self.log(f"=== pass start {'DRY RUN ' if self.dry else ''}state={self.state}")
        if not self.lock():
            return 0
        try:
            self.alexandria()
            tier_a = self.tier_a()
            due, why, flags = self.claude_due()
            self.log(f"B gate: {'run' if due else 'skip'} ({why})")
            if due:
                self.tier_b(flags, tier_a)
            else:
                self.status["claude"] = f"skipped: {why}"
            self.drain_alerts()
            self.failures()
            self.prune_logs()
        except Exception as e:  # log, count as a failure, never leave the lock behind
            self.status["errors"].append(f"runner: {type(e).__name__}: {self.scrub(str(e))[:200]}")
            self.log(f"RUNNER ERROR {type(e).__name__}: {self.scrub(str(e))[:300]}")
            self.failures()
        finally:
            self.status["finished_ct"] = datetime.now(CT).strftime("%Y-%m-%d %H:%M:%S CT")
            self.write_json("runner_status.json", self.status)
            self.hb_stop.set()
            self.lockmod.release("bg")
            self.log(f"=== pass end, {len(self.status['errors'])} errors")
        return 1 if self.status["errors"] else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--state-from")
    ap.add_argument("--force-claude", action="store_true")
    ap.add_argument("--no-claude", action="store_true")
    ap.add_argument("--model", default="sonnet")
    sys.exit(Pass(ap.parse_args()).go())
