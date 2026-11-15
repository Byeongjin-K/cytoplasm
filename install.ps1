# Installs or updates cytoplasm's default Dori setup on native Windows (no WSL).
#   powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/orientpine/cytoplasm/main/install.ps1 | iex"
# Run the same line again to update. Options: environment variables (irm | iex) or parameters (-File):
#   CYTO_ROLES / -Role (okdori,moodori,gongdori)   CYTO_OWNER_CALL / -OwnerCall   CYTO_HOME / -InstallRoot
#
# What it does: finds or installs git, Python 3.11+, bun, PowerShell 7 (winget, per user where it can) and omo, the agent
# each Dori runs (its own installer, get.omo.dev); clones
# cytoplasm into <InstallRoot>\src; on the first run checks the newest release tag's signature against the update trust
# key whose fingerprint is published in README (nothing from the clone runs before that); then hands over to
# `python -m automation.install.update`, which keeps that check for every later update and runs the Dori installer
# (pinned fork, herdr, Task Scheduler entries for the herdr server and `dori serve`). No administrator rights needed.
# This file stays ASCII: Windows PowerShell 5.1 reads a -File script without a BOM in the ANSI code page.
param(
  [string]$Repo = $(if ($env:CYTO_REPO) { $env:CYTO_REPO } else { "https://github.com/orientpine/cytoplasm.git" }),
  [string]$InstallRoot = $(if ($env:CYTO_HOME) { $env:CYTO_HOME } else { Join-Path $env:USERPROFILE ".cytoplasm" }),
  [string]$TrustKey = $env:CYTO_TRUST_KEY,
  [string]$ExpectFingerprint = $(if ($env:CYTO_EXPECT_FINGERPRINT) { $env:CYTO_EXPECT_FINGERPRINT } else { "SHA256:4yOZrMFmCSiSryt6+sal+E2hROpMlalgWNUqSf9lqsU" }),
  [string[]]$Role = $(if ($env:CYTO_ROLES) { $env:CYTO_ROLES -split "," } else { @() }),
  [string]$OwnerCall = $env:CYTO_OWNER_CALL
)
$ErrorActionPreference = "Stop"
$Principal = "update-trust@autophagy"

function Step($m) { Write-Host "== $m" }
function Have($c) { [bool](Get-Command $c -ErrorAction SilentlyContinue) }
function Native($command, [string[]]$arguments) {
  & $command @arguments
  if ($LASTEXITCODE -ne 0) { throw "CYTO-WINDOWS-FAIL: $command $($arguments -join ' ') (exit $LASTEXITCODE)" }
}
function RefreshPath {
  $env:Path = @([Environment]::GetEnvironmentVariable("Path", "Machine"), [Environment]::GetEnvironmentVariable("Path", "User"), $env:Path) -join ";"
}
function AddUserPath($dir) {
  $p = [Environment]::GetEnvironmentVariable("Path", "User")
  if (-not ($p -split ";" | Where-Object { $_ -eq $dir })) { [Environment]::SetEnvironmentVariable("Path", "$p;$dir", "User") }
  if (-not ($env:Path -split ";" | Where-Object { $_ -eq $dir })) { $env:Path = "$env:Path;$dir" }
}
function InstallWithWinget($id, $why) {
  if (-not (Have winget.exe)) { throw "CYTO-WINDOWS-NEEDS: $why. Install it ($id) and run this again" }
  Write-Host "Installing $id with winget ($why)"
  & winget.exe install --id $id --exact --source winget --accept-package-agreements --accept-source-agreements --silent
  RefreshPath
}
function FindPwsh {
  RefreshPath
  if (Have pwsh) { return $true }
  $installed = Join-Path $env:ProgramFiles "PowerShell\7\pwsh.exe"
  if (Test-Path $installed) { AddUserPath (Split-Path $installed); return $true }
  return $false
}
function FindPython {
  # a probe that writes to stderr ("no such runtime") must not end the script under Windows PowerShell 5.1
  # Each hit returns with the unary comma: PowerShell unrolls a one-element array, so a bare python.exe path came back as a
  # string and $py[0] was its first letter ("The term 'C' is not recognized" right after winget installed Python).
  $ErrorActionPreference = "Continue"
  foreach ($v in @("3.14", "3.13", "3.12", "3.11")) {
    if (Have py) { & py "-$v" -c "import sys" 2>$null; if ($LASTEXITCODE -eq 0) { return ,@("py", "-$v") } }
  }
  foreach ($c in @("python", "python3")) {
    $cmd = Get-Command $c -ErrorAction SilentlyContinue
    # the WindowsApps python.exe is a Store stub that opens the Store instead of running
    if ($cmd -and $cmd.Source -notlike "*\WindowsApps\*") {
      & $cmd.Source -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" 2>$null
      if ($LASTEXITCODE -eq 0) { return ,@($cmd.Source) }
    }
  }
  return $null
}

Step "git"
if (-not (Have git)) { InstallWithWinget "Git.Git" "git is needed to fetch cytoplasm and the Dori fork" }
Native "git" @("--version")

Step "Python 3.11+"
$py = FindPython
if (-not $py) { InstallWithWinget "Python.Python.3.12" "Python 3.11 or newer runs the installer"; $py = FindPython }
if (-not $py) { throw "CYTO-WINDOWS-NEEDS: Python 3.11 or newer (winget install Python.Python.3.12), then run this again" }
$pyArgs = @($py | Select-Object -Skip 1)
& $py[0] @pyArgs --version

Step "bun"
if (-not (Have bun)) { Invoke-RestMethod https://bun.sh/install.ps1 | Invoke-Expression; AddUserPath (Join-Path $env:USERPROFILE ".bun\bin") }
Native "bun" @("--version")

Step "PowerShell 7.3+ (herdr panes need it)"
if (-not (FindPwsh)) { InstallWithWinget "Microsoft.PowerShell" "Dori runs its herdr panes in PowerShell 7" }
if (-not (FindPwsh)) { throw "CYTO-WINDOWS-NEEDS: PowerShell 7.3 or newer (winget install Microsoft.PowerShell), then run this again" }

Step "omo (the agent the Dori lead runs in its herdr tab)"
if (-not (Have omo)) {
  Invoke-RestMethod https://get.omo.dev/install.ps1 | Invoke-Expression
  AddUserPath (Join-Path $env:USERPROFILE ".local\bin")
}
if (-not (Have omo)) { throw "CYTO-WINDOWS-NEEDS: omo (irm https://get.omo.dev/install.ps1 | iex), then run this again" }

$src = Join-Path $InstallRoot "src"
$signers = Join-Path $InstallRoot "update-allowed-signers"
$updateArgs = @("-m", "automation.install.update", "--state", $InstallRoot)
if (-not (Test-Path (Join-Path $src ".git")) -or -not (Test-Path $signers)) {
  Step "first install: newest signed release of $Repo"
  if (-not (Test-Path (Join-Path $src ".git"))) {
    New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
    Native "git" @("clone", "-q", "--no-checkout", $Repo, $src)
  }
  $work = Join-Path ([IO.Path]::GetTempPath()) ("cyto-trust-" + [Guid]::NewGuid().ToString("N"))
  New-Item -ItemType Directory -Path $work | Out-Null
  try {
    $key = $TrustKey
    if (-not $key) {
      $key = Join-Path $work "update-trust.pub"
      Invoke-WebRequest -UseBasicParsing "https://github.com/orientpine/cytoplasm/releases/latest/download/update-trust.pub" -OutFile $key
    }
    $line = (Get-Content -LiteralPath $key | Where-Object { $_.Trim() -and -not $_.StartsWith("#") } | Select-Object -First 1).Trim()
    $parts = $line -split "\s+"
    # the SHA256 fingerprint is base64 of the sha256 of the key blob, without padding, exactly as ssh-keygen -lf prints it
    $blob = [Convert]::FromBase64String($parts[1])
    $digest = [Security.Cryptography.SHA256]::Create().ComputeHash($blob)
    $actual = "SHA256:" + [Convert]::ToBase64String($digest).TrimEnd("=")
    if ($actual -ne $ExpectFingerprint) {
      throw "CYTO-WINDOWS-FINGERPRINT-MISMATCH: update trust key $actual is not the published $ExpectFingerprint - nothing from the clone was run"
    }
    Write-Host "CYTO-TRUST-KEY $actual"
    $allowed = Join-Path $work "allowed_signers"
    [IO.File]::WriteAllText($allowed, "$Principal namespaces=`"git`" $($parts[0]) $($parts[1])`n")
    $tags = & git -C $src ls-remote --tags --refs origin "v*"
    if ($LASTEXITCODE -ne 0) { throw "CYTO-WINDOWS-FAIL: git ls-remote $Repo" }
    $releases = @($tags | ForEach-Object { ($_ -split "\s+")[1] -replace "^refs/tags/", "" } |
      Where-Object { $_ -match '^v(\d+)\.(\d+)\.(\d+)$' } |
      Sort-Object { $v = $_.Substring(1).Split("."); [int]$v[0] * 1000000 + [int]$v[1] * 1000 + [int]$v[2] } -Descending)
    $chosen = $null
    foreach ($tag in $releases) {
      Native "git" @("-C", $src, "fetch", "-q", "--no-tags", "origin", "+refs/tags/${tag}:refs/tags/${tag}")
      # git reports a good signature on stderr; under 5.1 a redirected stderr line would otherwise stop the script
      $ErrorActionPreference = "Continue"
      $said = & git -C $src -c "gpg.ssh.allowedSignersFile=$allowed" verify-tag $tag 2>&1 | Out-String
      $ErrorActionPreference = "Stop"
      if ($LASTEXITCODE -eq 0 -and $said -match "Good `"git`" signature for $([regex]::Escape($Principal))") { $chosen = $tag; break }
      Write-Host "CYTO-UNSIGNED-SKIP $tag"
    }
    if (-not $chosen) { throw "CYTO-WINDOWS-NO-SIGNED-RELEASE: no tag of $Repo is signed by the update trust key" }
    Native "git" @("-C", $src, "checkout", "-q", "--detach", "refs/tags/$chosen")
    Write-Host "CYTO-RELEASE $chosen (signature checked)"
    $updateArgs += @("--trust-key", $key, "--expect-fingerprint", $ExpectFingerprint)
    foreach ($r in $Role) { if ($r) { $updateArgs += @("--role", $r.Trim()) } }
    if ($OwnerCall) { $updateArgs += @("--owner-call", $OwnerCall) }
    Step "install (python -m automation.install.update)"
    $env:PYTHONUTF8 = "1"; $env:PYTHONUNBUFFERED = "1"
    Push-Location $src
    try { & $py[0] @pyArgs @updateArgs; $code = $LASTEXITCODE } finally { Pop-Location }
  } finally {
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
  }
} else {
  Step "update (python -m automation.install.update)"
  $env:PYTHONUTF8 = "1"; $env:PYTHONUNBUFFERED = "1"
  Push-Location $src
  try { & $py[0] @pyArgs @updateArgs; $code = $LASTEXITCODE } finally { Pop-Location }
}
if ($code -ne 0) { throw "CYTO-WINDOWS-FAIL: automation.install.update exit $code" }
Write-Host ""
Write-Host "CYTO-WINDOWS-INSTALLED $src"
Write-Host "Update later: run the same line again. Open a new terminal so PATH picks up dori and herdr."
