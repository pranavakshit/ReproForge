param(
    [ValidateSet("hardware", "software")]
    [string]$Renderer = "hardware",
    [switch]$Build,
    [switch]$Api,
    [int]$Port = 8765,
    [switch]$Help
)

$ErrorActionPreference = "Stop"
$RepoRoot = $PSScriptRoot
Set-Location $RepoRoot

if ($Help) {
    Write-Host "ReproForge Unified Start Script" -ForegroundColor Cyan
    Write-Host "  .\start.ps1                      Launch ReproForge Desktop Studio (Hardware GPU)"
    Write-Host "  .\start.ps1 -Renderer software   Launch Desktop Studio in low-RAM CPU mode"
    Write-Host "  .\start.ps1 -Build               Rebuild release binary before launching"
    Write-Host "  .\start.ps1 -Api [-Port 8765]    Start optional headless FastAPI server"
    exit 0
}

if ($Api) {
    $UvicornExe = Join-Path $RepoRoot ".venv\Scripts\uvicorn.exe"
    $AlembicExe = Join-Path $RepoRoot ".venv\Scripts\alembic.exe"
    if (-not (Test-Path $UvicornExe)) {
        throw "Python virtual environment uvicorn not found at $UvicornExe. Run 'pip install -r requirements.txt' inside .venv."
    }
    $env:PYTHONPATH = $RepoRoot
    if (Test-Path $AlembicExe) {
        & $AlembicExe upgrade head | Out-Null
    }
    Write-Host "[ReproForge] Starting optional FastAPI server on http://127.0.0.1:$Port ..." -ForegroundColor Green
    & $UvicornExe backend.app.api.app:app --host 127.0.0.1 --port $Port
    exit $LASTEXITCODE
}

$LaunchScript = Join-Path $RepoRoot "scripts\launch_desktop.ps1"
& $LaunchScript -Renderer $Renderer -Build:$Build
exit $LASTEXITCODE
