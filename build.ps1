<#
.SYNOPSIS
    Build STS2_MCP.dll against the local Slay the Spire 2 installation.

.DESCRIPTION
    Hermetic build: every piece of dotnet/NuGet state is redirected into
    ./.build, so the build never touches %APPDATA%, %USERPROFILE% or
    %DOTNET_CLI_HOME%.  That is not only tidy -- it is what makes the build
    work inside the DSH sandbox, where those paths are read-only.

.PARAMETER GameDir
    Slay the Spire 2 install root (the folder containing SlayTheSpire2.exe).
    Resolution order: -GameDir, $env:STS2_GAME_DIR, the default Steam path.

.PARAMETER Configuration
    Debug or Release (default: Release).

.PARAMETER Install
    Copy the built DLL + manifest into <GameDir>\mods.  The game should be
    closed; writing there needs administrator rights.

.EXAMPLE
    .\build.ps1
    .\build.ps1 -GameDir "D:\SteamLibrary\steamapps\common\Slay the Spire 2"
    .\build.ps1 -Install
#>
[CmdletBinding()]
param(
    [string]$GameDir,
    [ValidateSet('Debug', 'Release')]
    [string]$Configuration = 'Release',
    [switch]$Install
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot

# --- locate the game -------------------------------------------------------
if (-not $GameDir) { $GameDir = $env:STS2_GAME_DIR }
if (-not $GameDir) {
    $GameDir = 'C:\Program Files (x86)\Steam\steamapps\common\Slay the Spire 2'
}

$dllDir = Join-Path $GameDir 'data_sts2_windows_x86_64'
$sts2Dll = Join-Path $dllDir 'sts2.dll'
if (-not (Test-Path -LiteralPath $sts2Dll)) {
    throw "sts2.dll not found under '$dllDir'. Pass -GameDir <install root>."
}

$project = Join-Path $root 'mod\sts2-bridge\STS2_MCP.csproj'
if (-not (Test-Path -LiteralPath $project)) {
    throw "Mod source missing: '$project'. Run: git clone https://github.com/enderzcx/sts2-bridge mod/sts2-bridge"
}

# --- hermetic dotnet/nuget state ------------------------------------------
$buildHome = Join-Path $root '.build'
$env:DOTNET_CLI_HOME = Join-Path $buildHome 'dotnet'
$env:NUGET_PACKAGES = Join-Path $buildHome 'nuget-packages'
$env:APPDATA = Join-Path $buildHome 'appdata'
$env:LOCALAPPDATA = Join-Path $buildHome 'localappdata'
$env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'
$env:DOTNET_NOLOGO = '1'

New-Item -ItemType Directory -Force -Path `
    $env:DOTNET_CLI_HOME, $env:NUGET_PACKAGES, $env:LOCALAPPDATA, (Join-Path $env:APPDATA 'NuGet') |
    Out-Null

# NuGet insists on reading a user-level config; give it an offline one.
Copy-Item -LiteralPath (Join-Path $root 'nuget.config') `
    -Destination (Join-Path $env:APPDATA 'NuGet\NuGet.Config') -Force

# --- build ----------------------------------------------------------------
$outDir = Join-Path $root 'out'

Write-Host "=== STS2_MCP ($Configuration) ===" -ForegroundColor Cyan
Write-Host "game   : $GameDir"
Write-Host "project: $project"
Write-Host "output : $outDir"
Write-Host ""

dotnet build $project -c $Configuration -o $outDir -p:STS2GameDir="$GameDir"
if ($LASTEXITCODE -ne 0) { throw "dotnet build failed with exit code $LASTEXITCODE" }

$dll = Join-Path $outDir 'STS2_MCP.dll'
if (-not (Test-Path -LiteralPath $dll)) { throw "Build reported success but '$dll' is missing." }

# --- optional install -----------------------------------------------------
if ($Install) {
    $modsDir = Join-Path $GameDir 'mods'
    New-Item -ItemType Directory -Force -Path $modsDir | Out-Null
    Copy-Item -LiteralPath $dll -Destination $modsDir -Force
    # The loader requires <mod-id>.json; mod_manifest.json must be renamed.
    Copy-Item -LiteralPath (Join-Path $root 'mod\sts2-bridge\mod_manifest.json') `
        -Destination (Join-Path $modsDir 'STS2_MCP.json') -Force
    Write-Host "Installed to $modsDir" -ForegroundColor Green
}

$mb = [math]::Round((Get-Item -LiteralPath $dll).Length / 1KB, 1)
Write-Host ""
Write-Host "OK  $dll ($mb KB)" -ForegroundColor Green
