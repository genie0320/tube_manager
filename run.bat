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

echo [TubeSSOT] Starting Streamlit Application on http://localhost:8501 ...
start http://localhost:8501
.\.venv\Scripts\streamlit.exe run app.py --server.port 8501 --server.headless false
pause
