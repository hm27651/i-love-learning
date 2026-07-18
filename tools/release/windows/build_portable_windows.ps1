param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..\..")
Set-Location $Root

& $Python -m pip install -r packaging\windows\requirements-portable.txt --disable-pip-version-check
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install Portable build dependencies."
}

$StagingRoot = [System.IO.Path]::GetFullPath((Join-Path $Root "build\portable-staging"))
$ExpectedStagingRoot = [System.IO.Path]::GetFullPath((Join-Path $Root "build"))
if (-not $StagingRoot.StartsWith($ExpectedStagingRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Portable staging path escaped the repository build directory."
}
$PackageDir = Join-Path $StagingRoot "I-Love-Learning-Portable"
$Archive = Join-Path $Root "dist\I-Love-Learning-Portable.zip"
if (Test-Path -LiteralPath $StagingRoot) {
    Remove-Item -LiteralPath $StagingRoot -Recurse -Force
}
if (Test-Path -LiteralPath $Archive) {
    Remove-Item -LiteralPath $Archive -Force
}
if (Test-Path -LiteralPath "$Archive.sha256") {
    Remove-Item -LiteralPath "$Archive.sha256" -Force
}

New-Item -ItemType Directory -Force -Path (Split-Path $Archive) | Out-Null
& $Python -m PyInstaller --clean --noconfirm --distpath $StagingRoot packaging\windows\I-Love-Learning.spec
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed to build the Portable package."
}

New-Item -ItemType Directory -Force -Path (Join-Path $PackageDir "data") | Out-Null
Copy-Item -LiteralPath "packaging\windows\README.txt" -Destination (Join-Path $PackageDir "README.txt") -Force
$VersionInfo = Get-Content -LiteralPath "packaging\windows\version.json" -Raw -Encoding utf8 | ConvertFrom-Json
$VersionInfo | Add-Member -NotePropertyName build_commit -NotePropertyValue ((git rev-parse --short=12 HEAD).Trim()) -Force
$VersionInfo | Add-Member -NotePropertyName build_time -NotePropertyValue ([DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")) -Force
$VersionJson = $VersionInfo | ConvertTo-Json -Depth 5
[System.IO.File]::WriteAllText(
    (Join-Path $PackageDir "version.json"),
    $VersionJson,
    (New-Object System.Text.UTF8Encoding($false))
)

Compress-Archive -Path (Join-Path $PackageDir "*") -DestinationPath $Archive
Get-FileHash -Algorithm SHA256 -LiteralPath $Archive |
    ForEach-Object { "$($_.Hash)  I-Love-Learning-Portable.zip" } |
    Set-Content -Encoding ascii -LiteralPath "$Archive.sha256"

Write-Host "Portable package created:"
Write-Host $Archive
Write-Host "$Archive.sha256"
