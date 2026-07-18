param(
    [int]$Port = 23459,
    [string]$Python = ".venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..\..")
$Archive = Join-Path $Root "dist\I-Love-Learning-Portable.zip"
$ExtractDir = Join-Path $env:TEMP ("i-love-learning-portable-package-" + [guid]::NewGuid().ToString("N"))
$PackageDir = $ExtractDir
$Exe = Join-Path $PackageDir "I-Love-Learning.exe"
$VersionFile = Join-Path $PackageDir "version.json"

if (-not (Test-Path -LiteralPath $Archive)) {
    throw "Portable archive not found. Run tools\release\windows\build_portable_windows.ps1 first."
}
Expand-Archive -LiteralPath $Archive -DestinationPath $ExtractDir
if (-not (Test-Path -LiteralPath $Exe)) { throw "Portable archive does not contain I-Love-Learning.exe." }
if (-not (Test-Path -LiteralPath $VersionFile)) { throw "Portable archive does not contain version.json." }
$VersionInfo = Get-Content -LiteralPath $VersionFile -Raw -Encoding utf8 | ConvertFrom-Json

$DataDir = Join-Path $env:TEMP ("i-love-learning-portable-test-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

$oldData = $env:STUDY_DATA_DIR
$oldBackup = $env:STUDY_BACKUP_DIR
$oldPort = $env:PORT
$env:STUDY_DATA_DIR = $DataDir
$env:STUDY_BACKUP_DIR = Join-Path $DataDir "backups"
$env:PORT = [string]$Port

$Process = $null
try {
    $Process = Start-Process -FilePath $Exe -ArgumentList "--serve --host 127.0.0.1 --port $Port" -PassThru -WindowStyle Hidden
    $healthy = $false
    for ($i = 0; $i -lt 45; $i++) {
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
            if ($health.database -eq "ok") {
                $healthy = $true
                break
            }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if (-not $healthy) {
        throw "Portable executable did not pass /health"
    }
    if ($health.version -ne $VersionInfo.version -or $health.commit -ne $VersionInfo.build_commit) {
        throw "Portable /health version metadata does not match version.json."
    }

    & $Python -c "import os, pathlib, sqlite3; p=pathlib.Path(os.environ['STUDY_DATA_DIR'])/'h3cse.db'; c=sqlite3.connect(p); assert c.execute('pragma integrity_check').fetchone()[0]=='ok'; assert c.execute('select count(*) from questions').fetchone()[0]==0; print(p)"

    $DataInPackage = Join-Path $PackageDir "data"
    $protected = @()
    if (Test-Path -LiteralPath $DataInPackage) {
        $protected += Get-ChildItem -Recurse -File $DataInPackage -Include *.db,*.sqlite,*.sqlite3,*.pdf,*.vce,*.doc,*.docx,*.xls,*.xlsx,*.csv
    }
    $protected += Get-ChildItem -File $PackageDir -Include *.db,*.sqlite,*.sqlite3,*.pdf,*.vce,*.doc,*.docx,*.xls,*.xlsx,*.csv
    if ($protected) {
        $protected | Select-Object FullName
        throw "Portable package contains protected data artifacts."
    }

    Write-Host "Portable smoke test passed."
} finally {
    if ($Process -and -not $Process.HasExited) {
        Stop-Process -Id $Process.Id -Force
    }
    $env:STUDY_DATA_DIR = $oldData
    $env:STUDY_BACKUP_DIR = $oldBackup
    $env:PORT = $oldPort
    Remove-Item -LiteralPath $DataDir -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $ExtractDir -Recurse -Force -ErrorAction SilentlyContinue
}
