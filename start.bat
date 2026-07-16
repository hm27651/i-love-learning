@echo off
setlocal
cd /d "%~dp0"

set "VENV=%~dp0.venv"
set "PYTHON=%~dp0.venv\Scripts\python.exe"
set "SERVER=%~dp0.venv\Scripts\waitress-serve.exe"
set "REQ_FILE=%~dp0requirements.txt"
set "REQ_MARKER=%~dp0.venv\.requirements.sha256"
set "APP_ROOT=%~dp0"
set "PORT=23456"

rem Prevent multiple copies from sharing the same SQLite database.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$listeners=@(Get-NetTCPConnection -LocalPort $env:PORT -State Listen -ErrorAction SilentlyContinue);" ^
  "$pids=@($listeners | Select-Object -ExpandProperty OwningProcess -Unique);" ^
  "$ours=@(Get-CimInstance Win32_Process | Where-Object {$pids -contains $_.ProcessId -and $_.CommandLine -like ('*'+$env:APP_ROOT+'*waitress-serve.exe*') -and $_.CommandLine -like '*app:app*'});" ^
  "if($ours.Count -gt 0){exit 10}; if($listeners.Count -gt 0){exit 11}; exit 0"
if errorlevel 11 goto :port_busy
if errorlevel 10 goto :already_running

if not exist "%PYTHON%" (
  echo First run: creating the local Python environment...
  py -3.11 -m venv "%VENV%" 2>nul
  if errorlevel 1 python -m venv "%VENV%"
  if errorlevel 1 goto :failed
)

for /f %%H in ('powershell -NoProfile -Command "(Get-FileHash -Algorithm SHA256 -LiteralPath $env:REQ_FILE).Hash"') do set "REQ_HASH=%%H"
set "INSTALLED_HASH="
if exist "%REQ_MARKER%" set /p INSTALLED_HASH=<"%REQ_MARKER%"
if not exist "%SERVER%" goto :install_requirements
if /I not "%REQ_HASH%"=="%INSTALLED_HASH%" goto :install_requirements
goto :launch

:install_requirements
echo Installing required packages...
"%PYTHON%" -m pip install -r "%REQ_FILE%" --disable-pip-version-check
if errorlevel 1 goto :failed
>"%REQ_MARKER%" echo %REQ_HASH%

:launch

echo.
echo I Love Learning is running.
echo Keep this window open while using the app.
echo Open in this PC: http://127.0.0.1:%PORT%
echo Press Ctrl+C to stop.
echo.

"%SERVER%" --listen=0.0.0.0:%PORT% app:app
if errorlevel 1 goto :failed
goto :eof

:already_running
echo.
echo I Love Learning is already running. A second copy was not started.
echo Open in this PC: http://127.0.0.1:%PORT%
echo.
pause
exit /b 0

:port_busy
echo.
echo Port %PORT% is already used by another program.
echo Close that program or change the app port before trying again.
echo.
pause
exit /b 2

:failed
echo.
echo Startup failed. Please keep this window open and send a screenshot of the error above.
pause
exit /b 1
