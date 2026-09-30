@echo off
setlocal
cd /d "%~dp0"

echo [TubeSSOT] Checking Python environment...

if not exist ".venv\Scripts\python.exe" (
    echo [TubeSSOT] Creating virtual environment (.venv)...
    python -m venv .venv
    if errorlevel 1 (
        echo [TubeSSOT] Failed to create virtual environment. Please ensure Python 3.10+ is installed.
        pause
        exit /b 1
    )
    echo [TubeSSOT] Installing dependencies from requirements.txt...
    .\.venv\Scripts\python.exe -m pip install --upgrade pip
    .\.venv\Scripts\pip.exe install -r requirements.txt
    if errorlevel 1 (
        echo [TubeSSOT] Failed to install dependencies.
        pause
        exit /b 1
    )
)

echo [TubeSSOT] Starting Streamlit Application...
.\.venv\Scripts\streamlit.exe run app.py
pause
