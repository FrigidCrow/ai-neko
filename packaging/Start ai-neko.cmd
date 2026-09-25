@echo off
cd /d "%~dp0"
ai-neko.exe start
if errorlevel 1 pause
