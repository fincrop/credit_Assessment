# Start FastAPI locally with the repo .conda env (Windows).
# Kills stale listeners on port 8000 first — avoids Next.js hitting a dead/hung uvicorn.
param(
    [int]$Port = 8000,
    [switch]$Reload
)

$ErrorActionPreference = "Stop"
$Root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$CondaPy = Join-Path $Root ".conda\python.exe"
$AppDir = Split-Path $PSScriptRoot -Parent

if (-not (Test-Path $CondaPy)) {
    Write-Error "Missing $CondaPy — create .conda first (see README)."
}

Write-Host "Freeing port $Port..."
Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue |
    ForEach-Object {
        if ($_.OwningProcess -and $_.OwningProcess -ne 0) {
            Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
        }
    }
Start-Sleep -Seconds 1

$args = @("-m", "uvicorn", "api.app:app", "--host", "127.0.0.1", "--port", "$Port")
if ($Reload) { $args += "--reload" }

Write-Host "Starting: $CondaPy $($args -join ' ')"
Set-Location $AppDir
& $CondaPy @args
