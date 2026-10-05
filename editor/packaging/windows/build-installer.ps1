[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$StageDir,
    [Parameter(Mandatory)][string]$OutputFile,
    [string]$Compiler = 'ISCC.exe',
    [switch]$TestIdentity
)
$ErrorActionPreference = 'Stop'
$stagePath = (Resolve-Path -LiteralPath $StageDir).Path
if (-not (Test-Path -LiteralPath (Join-Path $stagePath 'bin/JSON-API-Forge-Editor.exe') -PathType Leaf)) {
    throw 'StageDir must contain the Editor executable.'
}
$outputPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputFile)
$outputParent = Split-Path -Parent $outputPath
New-Item -ItemType Directory -Force -Path $outputParent | Out-Null
$outputName = [IO.Path]::GetFileNameWithoutExtension($outputPath)
$definitions = @("/DStageDir=$stagePath", "/DOutputDir=$outputParent", "/DOutputName=$outputName")
if ($TestIdentity) {
    $definitions += '/DProductName=JSON API Forge Editor Installer Test'
    $definitions += '/DProductId={CF14BF8F-B916-4E12-8F5B-1CD41FAF2CAB}'
}
& $Compiler @definitions (Join-Path $PSScriptRoot 'editor.iss')
if ($LASTEXITCODE -ne 0) { throw "Installer compiler failed: $LASTEXITCODE" }
if (-not (Test-Path -LiteralPath $outputPath -PathType Leaf) -or (Get-Item -LiteralPath $outputPath).Length -lt 1MB) {
    throw 'Installer was not generated or is truncated.'
}
