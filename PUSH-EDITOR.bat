@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "JAF_UPLOAD_SELF=%~f0"
set "JAF_CHECK_ONLY=%~1"
where powershell.exe >nul 2>&1
if errorlevel 1 (
    echo ERROR: Windows PowerShell is required.
    pause
    exit /b 1
)
powershell.exe -NoLogo -NoProfile -Command "$s=[IO.File]::ReadAllText($env:JAF_UPLOAD_SELF); $m='# JAF_POWERSHELL_PAYLOAD'; $i=$s.LastIndexOf($m,[StringComparison]::Ordinal); if($i -lt 0){exit 1}; & ([ScriptBlock]::Create($s.Substring($i+$m.Length)))"
set "JAF_RESULT=%ERRORLEVEL%"
echo.
pause
exit /b %JAF_RESULT%
# JAF_POWERSHELL_PAYLOAD
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
$repo = 'https://github.com/YoungLionOrganization/JSON-API-Forge.git'
$base = [IO.Path]::GetDirectoryName($env:JAF_UPLOAD_SELF)
$work = $null
$locationPushed = $false
$pushStarted = $false
$specs = @(@{ Branch='Editor'; Folder='.'; Marker='editor/CMakeLists.txt' })
function Invoke-Git {
    param([string[]]$GitArgs)
    & git @GitArgs
    if ($LASTEXITCODE -ne 0) { throw ('Git failed: ' + ($GitArgs -join ' ')) }
}
function Get-Source {
    param([string]$Folder)
    return $base
}
function Read-Manifest {
    param([string]$Source)
    $manifest = Join-Path $Source 'MANIFEST.sha256'
    if (-not (Test-Path -LiteralPath $manifest -PathType Leaf)) { throw "Manifest missing: $manifest" }
    $seen = @{}
    $entries = @()
    foreach ($line in [IO.File]::ReadAllLines($manifest)) {
        if ($line -notmatch '^([0-9a-fA-F]{64})  (.+)$') { throw "Invalid manifest line in $manifest" }
        $hash = $Matches[1]
        $relative = $Matches[2].Replace('\','/')
        if ($relative.StartsWith('/') -or $relative.Contains(':') -or ($relative.Split('/') -contains '..') -or ($relative.Split('/') -contains '.') -or ($relative.Split('/') -contains '') -or $relative -eq 'MANIFEST.sha256') {
            throw "Invalid manifest path: $relative"
        }
        if ($seen.ContainsKey($relative)) { throw "Duplicate manifest path: $relative" }
        $seen[$relative] = $true
        $entries += @{ Relative=$relative; Hash=$hash }
    }
    foreach ($required in @('VERSION','LICENSE','CONTRIBUTOR_LICENSE_AGREEMENT.md','TRADEMARK_POLICY.md','AI_USAGE_POLICY.md','LICENSE-HISTORY.md','LICENSE-METADATA.json')) {
        if (-not $seen.ContainsKey($required)) { throw "Required manifest entry missing: $required" }
    }
    return $entries
}
function Assert-Files {
    param([string]$Source, [object[]]$Entries)
    $errors = @()
    foreach ($entry in $Entries) {
        $path = Join-Path $Source $entry.Relative
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            $errors += ('Missing: ' + $entry.Relative)
        } elseif ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $entry.Hash) {
            $errors += ('SHA256 mismatch: ' + $entry.Relative)
        }
    }
    if ($errors.Count -gt 0) {
        foreach ($problem in $errors) { Write-Host ('  ' + $problem) -ForegroundColor Red }
        throw "Source verification failed: $Source. Extract the Editor ZIP into a NEW folder; do not mix old and new sources."
    }
}
try {
    Get-Command git -CommandType Application -ErrorAction Stop | Out-Null
    Write-Host 'JSON API Forge v0.5.2 - Editor branch uploader'
    Write-Host ('Repository: ' + $repo)
    Write-Host 'This uploads this Editor source folder to refs/heads/Editor.'
    Write-Host 'The source folder is read only. Uploading uses a temporary repository.'
    foreach ($spec in $specs) {
        $source = Get-Source $spec.Folder
        Write-Host ('Checking ' + $spec.Branch + ': ' + $source)
        if ([IO.File]::ReadAllText((Join-Path $source 'VERSION')).Trim() -ne '0.5.2') { throw "Wrong VERSION in $source" }
        if (-not (Test-Path -LiteralPath (Join-Path $source $spec.Marker))) { throw "Missing component: $($spec.Marker) in $source" }
        $entries = @(Read-Manifest $source)
        Assert-Files $source $entries
        $spec.Source = $source
        $spec.Entries = $entries
        Write-Host ('Verified ' + $entries.Count + ' files.')
    }
    if ($env:JAF_CHECK_ONLY -eq '--check') {
        Write-Host 'SUCCESS: Editor source package verified. No GitHub operation performed.'
        exit 0
    }
    Invoke-Git -GitArgs @('ls-remote',$repo,'HEAD')
    if ((Read-Host 'Type YUKLE to upload this Editor folder') -cne 'YUKLE') {
        Write-Host 'Cancelled. Nothing uploaded.'
        exit 0
    }
    $work = Join-Path ([IO.Path]::GetTempPath()) ('jaf-upload-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $work | Out-Null
    foreach ($spec in $specs) {
        $stage = Join-Path $work $spec.Branch
        New-Item -ItemType Directory -Path $stage | Out-Null
        Write-Host ('Preparing ' + $spec.Branch)
        # Copy exactly the verified release files. Caches and unrelated files stay out.
        foreach ($entry in $spec.Entries) {
            $target = Join-Path $stage $entry.Relative
            [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($target)) | Out-Null
            [IO.File]::Copy((Join-Path $spec.Source $entry.Relative),$target,$false)
        }
        [IO.File]::Copy((Join-Path $spec.Source 'MANIFEST.sha256'),(Join-Path $stage 'MANIFEST.sha256'),$false)
        Assert-Files $stage $spec.Entries
        Push-Location -LiteralPath $stage
        $locationPushed = $true
        Invoke-Git -GitArgs @('init','-b',$spec.Branch)
        Invoke-Git -GitArgs @('config','user.name','Cavanshirpro')
        Invoke-Git -GitArgs @('config','user.email','Cavanshirpro@users.noreply.github.com')
        Invoke-Git -GitArgs @('config','core.autocrlf','false')
        Invoke-Git -GitArgs @('config','core.safecrlf','false')
        Invoke-Git -GitArgs @('add','--all','--force')
        foreach ($entry in $spec.Entries) {
            $executable = $entry.Relative.EndsWith('.sh')
            if ($entry.Relative.EndsWith('.py')) {
                $reader = [IO.File]::OpenText((Join-Path $stage $entry.Relative))
                try { $first = $reader.ReadLine(); $executable = ($null -ne $first -and $first.StartsWith('#!')) } finally { $reader.Dispose() }
            }
            if ($executable) { Invoke-Git -GitArgs @('update-index','--chmod=+x','--',$entry.Relative) }
        }
        # Retain the existing Editor history as the new snapshot's parent.
        Invoke-Git -GitArgs @('fetch','--no-tags',$repo,'refs/heads/Editor')
        $parent = ((Invoke-Git -GitArgs @('rev-parse','FETCH_HEAD')) -join '').Trim()
        $tree = ((Invoke-Git -GitArgs @('write-tree')) -join '').Trim()
        $commit = ((Invoke-Git -GitArgs @('-c','commit.gpgsign=false','commit-tree',$tree,'-p',$parent,'-m','Fix Editor v0.5.2 workflow and installer layout')) -join '').Trim()
        Invoke-Git -GitArgs @('update-ref','refs/heads/Editor',$commit)
        $spec.Commit = ((Invoke-Git -GitArgs @('rev-parse','HEAD')) -join '').Trim()
        $spec.Stage = $stage
        Pop-Location
        $locationPushed = $false
    }
    Push-Location -LiteralPath $specs[0].Stage
    $locationPushed = $true
    Write-Host 'Uploading Editor...'
    $pushArgs = @('push',$repo,'refs/heads/Editor:refs/heads/Editor')
    $pushStarted = $true
    Invoke-Git -GitArgs $pushArgs
    $remoteLines = @(Invoke-Git -GitArgs @('ls-remote','--heads',$repo))
    foreach ($spec in $specs) {
        $expected = $spec.Commit + "`trefs/heads/" + $spec.Branch
        if ($remoteLines -notcontains $expected) { throw ('Remote commit verification failed: ' + $spec.Branch) }
    }
    Write-Host 'SUCCESS: Editor uploaded and remote commit verified.' -ForegroundColor Green
    exit 0
} catch {
    Write-Host ('ERROR: ' + $_.Exception.Message) -ForegroundColor Red
    if (-not $pushStarted) { Write-Host 'Nothing was uploaded.' }
    else { Write-Host 'Push or remote verification failed. Check the output above and GitHub before retrying.' }
    exit 1
} finally {
    if ($locationPushed) { Pop-Location }
    if ($null -ne $work -and (Test-Path -LiteralPath $work)) {
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }
}
