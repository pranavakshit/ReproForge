param(
    [ValidateSet("hardware", "software")]
    [string]$Renderer = "hardware",
    [switch]$Build
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$ReleaseExe = Join-Path $RepoRoot "desktop\target\release\reproforge_desktop.exe"

if (Test-Path $VenvPython) {
    $env:PYTHONPATH = $RepoRoot
    if (Test-Path (Join-Path $RepoRoot "alembic.ini")) {
        & (Join-Path $RepoRoot ".venv\Scripts\alembic.exe") upgrade head | Out-Null
    }
}

if ($Build -or -not (Test-Path $ReleaseExe)) {
    Write-Host "[ReproForge] Building native release desktop binary..." -ForegroundColor Cyan
    cargo build --release --manifest-path (Join-Path $RepoRoot "desktop\Cargo.toml")
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Write-Host "[ReproForge] Launching Desktop Studio (--renderer=$Renderer)..." -ForegroundColor Green
& $ReleaseExe "--renderer=$Renderer"
