[CmdletBinding()]
param([Parameter(Mandatory)][string]$ToolDirectory)
$ErrorActionPreference = 'Stop'
$toolRoot = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($ToolDirectory)
New-Item -ItemType Directory -Force -Path $toolRoot | Out-Null
$download = Join-Path $toolRoot 'innosetup-6.7.1.exe'
Invoke-WebRequest -Uri 'https://github.com/jrsoftware/issrc/releases/download/is-6_7_1/innosetup-6.7.1.exe' -OutFile $download
$expected = '4d11e8050b6185e0d49bd9e8cc661a7a59f44959a621d31d11033124c4e8a7b0'
if ((Get-FileHash -LiteralPath $download -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) {
    throw 'Inno Setup tool checksum mismatch.'
}
$compilerRoot = Join-Path $toolRoot 'compiler'
$process = Start-Process -FilePath $download -WindowStyle Hidden -Wait -PassThru -ArgumentList @(
    '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/CURRENTUSER', '/NOICONS', '/TASKS=""', ('/DIR="' + $compilerRoot + '"')
)
if ($process.ExitCode -ne 0) { throw "Compiler tool installation failed: $($process.ExitCode)" }
$compiler = Join-Path $compilerRoot 'ISCC.exe'
if (-not (Test-Path -LiteralPath $compiler -PathType Leaf)) { throw 'Installer compiler is missing.' }
Write-Output $compiler
