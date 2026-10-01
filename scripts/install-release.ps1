[CmdletBinding()]
param(
    [ValidatePattern('^[0-9A-Za-z._-]+$')][string]$Version = '0.5.2',
    [string]$Destination = '',
    [ValidatePattern('^[0-9A-Za-z._-]+/[0-9A-Za-z._-]+$')][string]$Repository = 'YoungLionOrganization/JSON-API-Forge'
)

$ErrorActionPreference = 'Stop'
$repositoryParts = $Repository.Split('/')
if ($repositoryParts[0] -in @('.', '..') -or $repositoryParts[1] -in @('.', '..')) {
    throw 'Repository owner and name must not be dot segments.'
}
$detectedArchitecture = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString()
$architecture = switch ($detectedArchitecture) {
    'X64' { 'x64' }
    # Windows ARM64 runs signed x64 applications through the platform's
    # compatibility layer. The native ARM64 dependency ecosystem does not yet
    # provide every frozen Forge dependency as a wheel.
    'Arm64' { 'x64' }
    default { throw "Unsupported Windows architecture: $_. Use scripts/install.ps1 for the portable Python build." }
}
if ($detectedArchitecture -eq 'Arm64') {
    Write-Warning 'Using the verified Windows x64 compatibility build on Windows ARM64.'
}
$platform = "windows-$architecture"
$asset = "JSON-API-Forge-v$Version-$platform.zip"
$base = "https://github.com/$Repository/releases/download/v$Version"
$target = if ($Destination) {
    [System.IO.Path]::GetFullPath($Destination)
} else {
    [System.IO.Path]::GetFullPath("JSON-API-Forge-v$Version")
}
if (Test-Path -LiteralPath $target) { throw "Destination already exists: $target" }
$parent = Split-Path -Parent $target
$temporaryRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("forge-install-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $temporaryRoot | Out-Null

try {
    $archive = Join-Path $temporaryRoot $asset
    $checksum = "$archive.sha256"
    Invoke-WebRequest -UseBasicParsing -Uri "$base/$asset" -OutFile $archive
    Invoke-WebRequest -UseBasicParsing -Uri "$base/$asset.sha256" -OutFile $checksum
    $expected = ((Get-Content -LiteralPath $checksum -TotalCount 1) -split '\s+')[0].ToLowerInvariant()
    if ($expected -notmatch '^[0-9a-f]{64}$') { throw 'Release checksum file is malformed.' }
    $actual = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $expected) { throw 'SHA-256 verification failed; nothing was installed.' }

    if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $staging = Join-Path $temporaryRoot 'unpacked'
    New-Item -ItemType Directory -Path $staging | Out-Null
    $stagingRoot = [System.IO.Path]::GetFullPath($staging).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    ) + [System.IO.Path]::DirectorySeparatorChar
    $seen = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    $expandedBytes = [long]0
    $zip = [System.IO.Compression.ZipFile]::OpenRead($archive)
    try {
        if ($zip.Entries.Count -eq 0 -or $zip.Entries.Count -gt 10000) {
            throw 'Release archive has an invalid entry count.'
        }
        foreach ($entry in $zip.Entries) {
            $relative = $entry.FullName.Replace('\', '/')
            $segments = $relative.TrimEnd('/').Split('/')
            if (
                [string]::IsNullOrWhiteSpace($relative) -or
                $relative.StartsWith('/') -or
                $relative -match '^[A-Za-z]:' -or
                $relative -match '(^|/)\.($|/)' -or
                $relative -match '(^|/)\.\.($|/)' -or
                $relative.IndexOf([char]0) -ge 0
            ) {
                throw "Release archive contains an unsafe path: $relative"
            }
            foreach ($segment in $segments) {
                if (
                    [string]::IsNullOrWhiteSpace($segment) -or
                    $segment -match '[<>:"|?*\x00-\x1F]' -or
                    $segment.EndsWith('.') -or
                    $segment.EndsWith(' ') -or
                    $segment -match '^(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?$'
                ) {
                    throw "Release archive contains an invalid Windows path: $relative"
                }
            }
            $unixType = (($entry.ExternalAttributes -shr 16) -band 0xF000)
            if ($unixType -eq 0xA000) {
                throw "Release archive contains a symbolic link: $relative"
            }
            $isDirectory = (
                $relative.EndsWith('/') -or
                (($entry.ExternalAttributes -band 0x10) -ne 0) -or
                $unixType -eq 0x4000
            )
            $entryTarget = [System.IO.Path]::GetFullPath((Join-Path $staging $relative))
            if (-not $entryTarget.StartsWith($stagingRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
                throw "Release archive escapes the destination: $relative"
            }
            if (-not $seen.Add($entryTarget)) {
                throw "Release archive contains a duplicate path: $relative"
            }
            $expandedBytes += $entry.Length
            if ($entry.Length -gt 1GB -or $expandedBytes -gt 2GB) {
                throw 'Release archive exceeds the safe extraction limit.'
            }
            if ($isDirectory) {
                New-Item -ItemType Directory -Path $entryTarget -Force | Out-Null
                continue
            }
            $entryParent = Split-Path -Parent $entryTarget
            if (-not (Test-Path -LiteralPath $entryParent)) {
                New-Item -ItemType Directory -Path $entryParent -Force | Out-Null
            }
            $sourceStream = $entry.Open()
            try {
                $targetStream = [System.IO.File]::Open(
                    $entryTarget,
                    [System.IO.FileMode]::CreateNew,
                    [System.IO.FileAccess]::Write,
                    [System.IO.FileShare]::None
                )
                try { $sourceStream.CopyTo($targetStream) }
                finally { $targetStream.Dispose() }
            }
            finally { $sourceStream.Dispose() }
        }
    }
    finally {
        $zip.Dispose()
    }
    if (
        -not (Test-Path -LiteralPath (Join-Path $staging 'bin/forge.exe') -PathType Leaf) -or
        -not (Test-Path -LiteralPath (Join-Path $staging 'bin/forge-server.exe') -PathType Leaf)
    ) {
        throw 'Release archive does not contain the expected executables.'
    }
    New-Item -ItemType Directory -Path $target | Out-Null
    Get-ChildItem -LiteralPath $staging -Force | Copy-Item -Destination $target -Recurse
    Write-Host "Installed verified JSON API Forge v$Version for $platform in $target"
    Write-Host "Run '$target\bin\forge.exe --help' or '$target\bin\forge-server.exe --root YOUR_DEPLOYMENT_ROOT'."
}
finally {
    if (Test-Path -LiteralPath $temporaryRoot) {
        Remove-Item -LiteralPath $temporaryRoot -Recurse -Force
    }
}
