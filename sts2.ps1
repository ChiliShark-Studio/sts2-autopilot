<#
.SYNOPSIS
    Launcher for sts2-autopilot.

.DESCRIPTION
    Finds a *working* Python (PATH, or the one bundled with DSH), switches to the
    project directory and runs `python -m brain.cli <args>`.

    Candidates are executed, not just checked for existence: Windows ships a
    Store stub named python.exe that only prints "Python was not found".

    ASCII-only on purpose: Windows PowerShell reads BOM-less .ps1 files as ANSI,
    and non-ASCII strings break the parser.

.EXAMPLE
    .\sts2.ps1 fight --encounter lagavulin --runs 30
    .\sts2.ps1 fight-live
    .\sts2.ps1 run --offline --steps 100
#>
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$CliArgs
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot

function Test-WorkingPython([string]$exe) {
    if (-not $exe -or -not (Test-Path -LiteralPath $exe)) { return $false }
    try {
        $out = & $exe -c "import sys;print(sys.version_info[0])" 2>$null
        return ($LASTEXITCODE -eq 0 -and "$out".Trim() -eq '3')
    } catch {
        return $false
    }
}

$candidates = @()
$onPath = Get-Command python -ErrorAction SilentlyContinue
if ($onPath) { $candidates += $onPath.Source }
$candidates += Join-Path $env:USERPROFILE '.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe'
$candidates += "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
$candidates += "$env:ProgramFiles\Python312\python.exe"

$python = $null
foreach ($candidate in $candidates) {
    if (Test-WorkingPython $candidate) { $python = $candidate; break }
}

if (-not $python) {
    Write-Host "No working Python 3 found. Install Python 3.10+ and tick 'Add to PATH'." -ForegroundColor Red
    exit 1
}

$env:PYTHONIOENCODING = 'utf-8'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }
Set-Location -LiteralPath $root

& $python -m brain.cli @CliArgs
exit $LASTEXITCODE
