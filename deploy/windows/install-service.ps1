[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)][string]$InstallDirectory,
    [Parameter(Mandatory)][string]$DeploymentRoot,
    [string]$ServiceName = 'JSONAPIForge',
    [int]$Port = 8000
)

$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script from an elevated PowerShell session.'
}
if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
    throw "Service already exists: $ServiceName"
}
$server = (Resolve-Path -LiteralPath (Join-Path $InstallDirectory 'bin\forge-server.exe')).Path
$root = (Resolve-Path -LiteralPath $DeploymentRoot).Path
if ($Port -lt 1 -or $Port -gt 65535) { throw 'Port must be between 1 and 65535.' }
$binaryPath = '"{0}" --root "{1}" --host 127.0.0.1 --port {2}' -f $server, $root, $Port

if ($PSCmdlet.ShouldProcess($ServiceName, 'Create Windows service')) {
    New-Service -Name $ServiceName -BinaryPathName $binaryPath -DisplayName 'JSON API Forge v0.5.3' -StartupType Automatic
    & sc.exe failure $ServiceName reset= 86400 actions= restart/5000/restart/15000/''/0 | Out-Null
    Write-Host "Created $ServiceName. Grant its service account only the required ACLs, then start it with: Start-Service $ServiceName"
}
