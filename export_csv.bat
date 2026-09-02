@echo off
setlocal
cd /d "%~dp0"
set "PYTHON_EXE=python"
if exist "%~dp0.venv\Scripts\python.exe" set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
"%PYTHON_EXE%" -m storage_optimiser.cli export --format csv
endlocal
