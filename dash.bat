@echo off
rem Start the F1 strategy dashboard (open the URL it prints).
cd /d "%~dp0"
.venv\Scripts\streamlit.exe run dashboard/Home.py
