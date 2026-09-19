<#
Start TieOut's local UI on Windows, setting everything up the first time.

    .\run.ps1                 # open the page on http://127.0.0.1:8765/
    .\run.ps1 --port 9000     # any tieout-ui flag passes straight through
    .\run.ps1 --no-open

Safe to run every time. The first run builds a virtual environment and
installs; later runs skip straight to starting the server, and reinstall only
when something under the packages has changed since the last one.

If PowerShell refuses to run this because of the execution policy, either

    powershell -ExecutionPolicy Bypass -File .\run.ps1

or set it once for yourself:

    Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
#>

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$Venv = '.venv'
$Marker = Join-Path $Venv '.tieout-ui-installed'
$VenvPython = Join-Path $Venv 'Scripts\python.exe'
$VenvUi = Join-Path $Venv 'Scripts\tieout-ui.exe'

function Die($message) {
    Write-Host ''
    Write-Host "error $message" -ForegroundColor Red
    Write-Host ''
    exit 1
}

function Test-Usable($exe, $prefix) {
    # `py -3.12` is two tokens, so the version argument is passed separately.
    try {
        $arguments = @()
        if ($prefix) { $arguments += $prefix }
        $arguments += @('-c', 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)')
        & $exe @arguments 2>$null | Out-Null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

# -- 1. find an interpreter new enough -------------------------------------- #
#
# The py launcher first, because it is the one thing on Windows that reliably
# knows about every Python installed rather than whichever landed on PATH last.

$python = $null
$pythonArgs = @()
foreach ($candidate in @(
        @{ Exe = 'py';     Prefix = '-3.14' },
        @{ Exe = 'py';     Prefix = '-3.13' },
        @{ Exe = 'py';     Prefix = '-3.12' },
        @{ Exe = 'py';     Prefix = '-3.11' },
        @{ Exe = 'py';     Prefix = '-3' },
        @{ Exe = 'python'; Prefix = $null })) {
    if (Get-Command $candidate.Exe -ErrorAction SilentlyContinue) {
        if (Test-Usable $candidate.Exe $candidate.Prefix) {
            $python = $candidate.Exe
            if ($candidate.Prefix) { $pythonArgs = @($candidate.Prefix) }
            break
        }
    }
}

if (-not $python) {
    Write-Host ''
    Write-Host 'TieOut needs Python 3.11 or newer.'
    Write-Host ''
    Write-Host '  Install it from https://www.python.org/downloads/'
    Write-Host '  and tick "Add python.exe to PATH" in the installer.'
    Write-Host ''
    Die 'no usable Python. Nothing has been changed.'
}

# -- 2. the virtual environment --------------------------------------------- #

if ((Test-Path $VenvPython) -and -not (Test-Usable $VenvPython $null)) {
    Die "$Venv was built with a Python older than 3.11.
      Delete it and run this again:  Remove-Item -Recurse -Force $Venv; .\run.ps1"
}

if (-not (Test-Path $VenvPython)) {
    Write-Host "Setting up $Venv. This happens once."
    & $python @pythonArgs -m venv $Venv
    if ($LASTEXITCODE -ne 0) { Die 'could not create a virtual environment.' }
}

# -- 3. install, but only when something changed ---------------------------- #

# A pyproject.toml change alone is not enough: the install below is a copy, not
# a link, and the console script's sys.path puts site-packages ahead of this
# directory. After a pull brings new code with an unchanged pyproject.toml the
# marker is still the newer file, the install is skipped, and the server serves
# the previous copy -- the reader's change appears to do nothing.
$needsInstall = -not (Test-Path $Marker)
if (-not $needsInstall) {
    $markerTime = (Get-Item $Marker).LastWriteTimeUtc
    $sources = @('pyproject.toml', 'tieout', 'tieout_ui', 'tieout_review', 'tieout_fix') |
        Where-Object { Test-Path $_ } |
        ForEach-Object { Get-ChildItem -Path $_ -Recurse -File -ErrorAction SilentlyContinue }
    $needsInstall = [bool]($sources | Where-Object { $_.LastWriteTimeUtc -gt $markerTime } | Select-Object -First 1)
}

if ($needsInstall) {
    Write-Host 'Installing TieOut and its UI dependencies. This happens on a first run and after an update.'
    # A plain (non-editable) install: editable mode needs pip 21.3+, and the pip
    # bundled with an older Python fails with a message about setuptools that
    # tells the reader nothing. Upgrading pip is worth trying, not worth failing
    # over.
    & $VenvPython -m pip install -q --upgrade pip 2>$null | Out-Null
    & $VenvPython -m pip install -q '.[ui]'
    if ($LASTEXITCODE -ne 0) { Die 'the install failed. The output above says why.' }
    New-Item -ItemType File -Path $Marker -Force | Out-Null
}

# pip can report success and still leave no console script -- most often when
# run.ps1 has been copied somewhere without the rest of the project.
if (-not (Test-Path $VenvUi)) {
    Die "installed, but $VenvUi is not there.
      Run this from inside a TieOut checkout -- the directory holding
      pyproject.toml and the tieout\ package. Currently: $PWD"
}

# -- 4. go ------------------------------------------------------------------ #

& $VenvUi @args
exit $LASTEXITCODE
