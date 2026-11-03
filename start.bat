@echo off
rem Start PITWALL: the API (api.bat, port 8000) and the React dev server (port 5173),
rem each in its own window. Then open http://localhost:5173
cd /d "%~dp0"
start "PITWALL API" cmd /k api.bat
start "PITWALL web" cmd /k "cd /d web && npm run dev"
