@echo off
setlocal
cd /d "%~dp0..\.."

set "PYTHON=%CD%\.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

echo [1/3] Checking the Git index and complete history...
"%PYTHON%" tools\safety\check_public_repo.py
if errorlevel 1 goto :failed

echo.
echo [2/3] Checking Python syntax...
"%PYTHON%" -m py_compile app.py migrations.py import_service.py knowledge_service.py transfer_service.py services\core\common_service.py services\core\project_service.py services\core\session_service.py services\core\stats_service.py services\core\storage_service.py services\questions\question_service.py services\imports\import_service.py services\knowledge\knowledge_common.py services\knowledge\knowledge_duplicates.py services\knowledge\knowledge_delete_service.py services\transfer\transfer_common.py services\transfer\export_service.py services\transfer\share_package_service.py
if errorlevel 1 goto :failed

echo.
echo [3/3] Running automated tests...
"%PYTHON%" -m unittest discover -s tests -v
if errorlevel 1 goto :failed

echo.
echo Public repository check passed. Ignored local data was not changed.
if /i not "%~1"=="--no-pause" pause
exit /b 0

:failed
echo.
echo Public repository check failed. Nothing in data was deleted or modified.
if /i not "%~1"=="--no-pause" pause
exit /b 1
