param(
    [int]$Port = 23459,
    [string]$Python = ".venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..\..")
$PackageDir = Join-Path $Root "dist\I-Love-Learning-Portable"
$Exe = Join-Path $PackageDir "I-Love-Learning.exe"

if (-not (Test-Path -LiteralPath $Exe)) {
    throw "Portable executable not found. Run tools\release\windows\build_portable_windows.ps1 first."
}

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
}
