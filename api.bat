@echo off
rem Start the PITWALL API on http://localhost:8000 (docs at /docs).
cd /d "%~dp0"
.venv\Scripts\uvicorn.exe api.main:app --reload --port 8000
