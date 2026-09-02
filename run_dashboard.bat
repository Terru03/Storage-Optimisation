@echo off
setlocal
cd /d "%~dp0"
set "PYTHON_EXE=python"
if exist "%~dp0.venv\Scripts\python.exe" set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
"%PYTHON_EXE%" -m streamlit run storage_optimiser\dashboard.py
endlocal
