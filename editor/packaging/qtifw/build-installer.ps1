[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateScript({ Test-Path $_ -PathType Container })]
    [string]$StageDir,

    [Parameter(Mandatory = $true)]
    [string]$OutputFile,

    [string]$BinaryCreator = "binarycreator.exe"
)

$ErrorActionPreference = "Stop"
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "../../..")).Path
$stagePath = (Resolve-Path $StageDir).Path
$outputPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputFile)
if (-not (Test-Path -LiteralPath (Join-Path $stagePath "bin/JSON-API-Forge-Editor.exe") -PathType Leaf)) {
    throw "StageDir must contain bin/JSON-API-Forge-Editor.exe; refusing an empty installer."
}
$outputParent = Split-Path -Parent $outputPath
New-Item -ItemType Directory -Force $outputParent | Out-Null

$workRoot = Join-Path ([IO.Path]::GetTempPath()) ("json-api-forge-ifw-" + [Guid]::NewGuid().ToString("N"))
$configRoot = Join-Path $workRoot "config"
$metaRoot = Join-Path $workRoot "packages/dev.jsonapiforge.editor/meta"
$dataRoot = Join-Path $workRoot "packages/dev.jsonapiforge.editor/data"

try {
    New-Item -ItemType Directory -Force $configRoot, $metaRoot, $dataRoot | Out-Null
    Copy-Item (Join-Path $PSScriptRoot "config/config.xml") -Destination $configRoot
    Copy-Item (Join-Path $PSScriptRoot "config/installer.qss") -Destination $configRoot
    Copy-Item (Join-Path $repositoryRoot "editor/resources/forge-editor.ico") -Destination $configRoot
    Copy-Item (Join-Path $repositoryRoot "editor/resources/brand-mark-transparent.png") `
        -Destination (Join-Path $configRoot "installer-window-icon.png")
    Copy-Item (Join-Path $PSScriptRoot "packages/dev.jsonapiforge.editor/meta/package.xml") -Destination $metaRoot
    Copy-Item (Join-Path $PSScriptRoot "packages/dev.jsonapiforge.editor/meta/installscript.qs") -Destination $metaRoot
    Copy-Item (Join-Path $repositoryRoot "LICENSE") -Destination (Join-Path $metaRoot "license.txt")
    Copy-Item (Join-Path $stagePath "*") -Destination $dataRoot -Recurse -Force

    $binaryCreatorPath = (Get-Command $BinaryCreator -ErrorAction Stop).Source
    & $binaryCreatorPath -f -c (Join-Path $configRoot "config.xml") -p (Join-Path $workRoot "packages") $outputPath
    if ($LASTEXITCODE -ne 0) {
        throw "Qt IFW binarycreator failed with exit code $LASTEXITCODE"
    }
    if (-not (Test-Path $outputPath -PathType Leaf) -or (Get-Item $outputPath).Length -lt 1MB) {
        throw "Qt IFW installer was not created or appears truncated: $outputPath"
    }
}
finally {
    if (Test-Path $workRoot -PathType Container) {
        Remove-Item $workRoot -Recurse -Force
    }
}
