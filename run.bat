@echo off
cd /d "%~dp0"
python -m tts_app.main
if errorlevel 1 pause
