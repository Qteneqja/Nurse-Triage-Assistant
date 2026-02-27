@echo off
echo ========================================
echo Starting Triage API Server
echo ========================================
echo.

cd /d "%~dp0"

echo Setting environment variables...
REM API key is read from .env file — do NOT hardcode secrets here.
REM Copy .env.example to .env and fill in DEEPSEEK_API_KEY.

echo.
echo Starting FastAPI server on http://127.0.0.1:8000
echo Press Ctrl+C to stop the server
echo ========================================
echo.

".venv\Scripts\python.exe" -m uvicorn src.main:app --host 127.0.0.1 --port 8000 --reload

pause
