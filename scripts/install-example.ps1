param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")]
    [string]$Name,
    [string]$Destination
)

$ErrorActionPreference = "Stop"
function Assert-NoReparseParents([string]$Path) {
    $Current = [IO.Path]::GetFullPath($Path)
    while ($Current) {
        $Item = Get-Item -LiteralPath $Current -Force -ErrorAction SilentlyContinue
        if ($Item -and ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Refusing a symbolic link or junction: $Current"
        }
        $Parent = [IO.Path]::GetDirectoryName($Current)
        if ($Parent -eq $Current) { break }
        $Current = $Parent
    }
}
$RepositoryRoot = Split-Path -Parent $PSScriptRoot
if (-not $Destination) {
    $Destination = Join-Path $RepositoryRoot "app"
}
$Source = Join-Path (Join-Path $RepositoryRoot "app") $Name
$Target = Join-Path $Destination $Name
Assert-NoReparseParents $Source
Assert-NoReparseParents $Target

if (-not (Test-Path -LiteralPath (Join-Path $Source "app.json") -PathType Leaf)) {
    throw "Unknown example: $Name"
}
$Links = @(Get-ChildItem -LiteralPath $Source -Recurse -Force | Where-Object {
    $_.Attributes -band [IO.FileAttributes]::ReparsePoint
})
if ($Links) {
    throw "Refusing to copy an example that contains symbolic links: $Name"
}
if (Test-Path -LiteralPath $Target) {
    throw "Refusing to overwrite existing target: $Target"
}

New-Item -ItemType Directory -Force -Path $Destination | Out-Null
$Staging = Join-Path $Destination (".forge-example-" + [Guid]::NewGuid().ToString('N'))
try {
    New-Item -ItemType Directory -Path $Staging | Out-Null
    Copy-Item -LiteralPath $Source -Destination (Join-Path $Staging $Name) -Recurse
    Assert-NoReparseParents $Target
    [IO.Directory]::Move((Join-Path $Staging $Name), [IO.Path]::GetFullPath($Target))
} finally {
    if (Test-Path -LiteralPath $Staging) { Remove-Item -LiteralPath $Staging -Recurse -Force }
}
Write-Host "Installed $Name at $Target"
Write-Host "Next: run 'forge init', 'forge validate', and 'forge dev' in the destination checkout."
