"""Push a P1 alert to Nat: a Windows toast on the laptop plus a post to the BARK Slack channel.

Usage (also imported by runner.py): python bg/alert.py "<title>" "<one line>"
Slack uses SLACK_WEBHOOK_URL from timebot/.env (the BARK webhook court-watch uses); skipped if unset.
The webhook URL is never printed. Messages are short plain text with no secrets or email bodies.
"""
import json
import os
import subprocess
import sys
import urllib.request

NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW
PS_APP = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
TOAST = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$x = New-Object Windows.Data.Xml.Dom.XmlDocument
$x.LoadXml('<toast><visual><binding template="ToastGeneric"><text></text><text></text></binding></visual></toast>')
$t = $x.GetElementsByTagName('text')
$t.Item(0).AppendChild($x.CreateTextNode($env:WBG_TITLE)) | Out-Null
$t.Item(1).AppendChild($x.CreateTextNode($env:WBG_BODY)) | Out-Null
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:WBG_APP).Show([Windows.UI.Notifications.ToastNotification]::new($x))
"""


def toast(title, body):
    env = {**os.environ, "WBG_TITLE": title[:120], "WBG_BODY": body[:300], "WBG_APP": PS_APP}
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", TOAST], env=env,
                       capture_output=True, text=True, timeout=30, creationflags=NO_WINDOW)
    return r.returncode == 0


def slack(webhook, text):
    if not webhook:
        return False
    req = urllib.request.Request(webhook, data=json.dumps({"text": text}).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status == 200
    except OSError:
        return False


def send(title, body, webhook=None, dry=False):
    """Returns a short result string for the log."""
    if dry:
        return "dry run: not sent"
    t = toast(title, body)
    s = slack(webhook, f"*[WATCHER-BG] {title}*\n{body}")
    return f"toast {'ok' if t else 'FAILED'}, slack {'ok' if s else ('skipped' if not webhook else 'FAILED')}"


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from _trello import load_env
    print(send(sys.argv[1], sys.argv[2], load_env().get("SLACK_WEBHOOK_URL")))
