<#
.SYNOPSIS
  Peek at a client's GitHub repo to tie a TEMPO deployment entry to the project/work it shipped.

.DESCRIPTION
  For a TEMPO billing audit: given a repo (or a client name to search) and a deploy date,
  shows the repo description, each branch's HEAD commit, and the commits that landed in a
  window around the deploy date. The branch names + commit messages reveal which project /
  change-order / support work a deployment was releasing -> so you can classify it as
  Project / Support-Billable / Warranty (non-billable) / Operations (pipeline setup).

  Requires the GitHub CLI (`gh`) authenticated with read access to SavvyOtterInc.
  (Uses ConvertFrom-Json rather than `gh --jq` to avoid Windows PowerShell 5.1's
   native-arg quoting bug with embedded double quotes.)

.EXAMPLE
  .\peek_deploy.ps1 -Client greenery
  .\peek_deploy.ps1 -Repo SmartLink -Date 2026-05-13
  .\peek_deploy.ps1 -Repo Greenery-Paylocity -Date 2026-05-08 -Window 5
#>
param(
  [string]$Repo,
  [string]$Client,
  [string]$Date,
  [int]$Window = 7,
  [string]$Owner = "SavvyOtterInc"
)

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
  Write-Error "GitHub CLI (gh) not found. Install from https://cli.github.com/"; exit 1
}

function Gh-Json([string]$endpoint) {
  $raw = gh api $endpoint 2>$null
  if (-not $raw) { return $null }
  try { return $raw | ConvertFrom-Json } catch { return $null }
}
function FirstLine([string]$msg) { ($msg -split "`n")[0] }

if ($Client) {
  Write-Host "Repos under $Owner matching '$Client':" -ForegroundColor Cyan
  $hits = gh search repos --owner $Owner $Client --limit 15 --json fullName,description 2>$null | ConvertFrom-Json
  $hits | ForEach-Object { "  {0,-45} {1}" -f $_.fullName, $_.description }
  Write-Host "`nRe-run with -Repo <name> -Date <yyyy-MM-dd> to inspect a deploy window." -ForegroundColor DarkGray
  return
}

if (-not $Repo) { Write-Error "Provide -Repo <name> (or -Client <search> to find one)."; exit 1 }

$full = "$Owner/$Repo"
Write-Host "======== $full ========" -ForegroundColor Cyan
$meta = Gh-Json "repos/$full"
if (-not $meta) { Write-Error "Repo $full not found or no access."; exit 1 }
"desc:    " + $(if ($meta.description) { $meta.description } else { "(none)" })
"default: " + $meta.default_branch
"pushed:  " + $meta.pushed_at.Substring(0,10)

Write-Host "`n-- branches (HEAD commit) --" -ForegroundColor Yellow
$branches = (Gh-Json "repos/$full/branches?per_page=100").name
foreach ($b in $branches) {
  $c = Gh-Json "repos/$full/commits/$b"
  if ($c) { "  {0,-34} {1}  {2}" -f $b, $c.commit.author.date.Substring(0,10), (FirstLine $c.commit.message) }
}

if ($Date) {
  try { $d = [datetime]::ParseExact($Date, 'yyyy-MM-dd', $null) } catch { Write-Error "Bad -Date '$Date' (use yyyy-MM-dd)"; exit 1 }
  $since = $d.AddDays(-$Window).ToString('yyyy-MM-dd')
  $until = $d.AddDays($Window).ToString('yyyy-MM-dd')
  Write-Host "`n-- commits in $since .. $until (branches with activity) --" -ForegroundColor Yellow
  foreach ($b in $branches) {
    $commits = Gh-Json "repos/$full/commits?sha=$b&since=${since}T00:00:00Z&until=${until}T23:59:59Z&per_page=30"
    if ($commits) {
      Write-Host "  [$b]" -ForegroundColor DarkGray
      $commits | ForEach-Object { "    {0}  {1}" -f $_.commit.author.date.Substring(0,10), (FirstLine $_.commit.message) }
    }
  }
}
