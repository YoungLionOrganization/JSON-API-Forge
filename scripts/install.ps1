[CmdletBinding()]
param(
    [switch]$Dev,
    [switch]$Release,
    [string]$Destination = ''
)

$ErrorActionPreference = "Stop"
if ($Dev -and $Release) { throw '-Dev and -Release cannot be combined.' }
if ($Release) {
    & (Join-Path $PSScriptRoot 'install-release.ps1') -Destination $Destination
    exit $LASTEXITCODE
}
$python = if ($env:PYTHON) { $env:PYTHON } else { "python" }
& $python -c 'import sys; raise SystemExit(0 if (3, 10) <= sys.version_info[:2] <= (3, 14) else "JSON API Forge requires Python 3.10-3.14")'
& $python -m venv .venv
$venvPython = Join-Path $PWD ".venv\Scripts\python.exe"
& $venvPython -m pip install --upgrade pip
$target = if ($Dev) { ".[dev]" } else { "." }
& $venvPython -m pip install -e $target
& $venvPython -m pip check
Write-Host "Installed JSON API Forge into .venv"
Write-Host "Activate it, then run: forge new MyService --slug my-service"
