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

$PackageDir = Join-Path $Root "dist\I-Love-Learning-Portable"
$Archive = Join-Path $Root "dist\I-Love-Learning-Portable.zip"
if (Test-Path -LiteralPath $PackageDir) {
    Remove-Item -LiteralPath $PackageDir -Recurse -Force
}
if (Test-Path -LiteralPath $Archive) {
    Remove-Item -LiteralPath $Archive -Force
}
if (Test-Path -LiteralPath "$Archive.sha256") {
    Remove-Item -LiteralPath "$Archive.sha256" -Force
}

& $Python -m PyInstaller --clean --noconfirm packaging\windows\I-Love-Learning.spec
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed to build the Portable package."
}

New-Item -ItemType Directory -Force -Path (Join-Path $PackageDir "data") | Out-Null
Copy-Item -LiteralPath "packaging\windows\README.txt" -Destination (Join-Path $PackageDir "README.txt") -Force
Copy-Item -LiteralPath "packaging\windows\version.json" -Destination (Join-Path $PackageDir "version.json") -Force

Compress-Archive -Path (Join-Path $PackageDir "*") -DestinationPath $Archive
Get-FileHash -Algorithm SHA256 -LiteralPath $Archive |
    ForEach-Object { "$($_.Hash)  I-Love-Learning-Portable.zip" } |
    Set-Content -Encoding ascii -LiteralPath "$Archive.sha256"

Write-Host "Portable package created:"
Write-Host $Archive
Write-Host "$Archive.sha256"
