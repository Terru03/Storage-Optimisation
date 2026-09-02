@echo off
setlocal
cd /d "%~dp0"
set "PYTHON_EXE=python"
if exist "%~dp0.venv\Scripts\python.exe" set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
set MODE=%~1
if "%MODE%"=="" set MODE=test
"%PYTHON_EXE%" -m storage_optimiser.cli scan --mode %MODE%
endlocal
