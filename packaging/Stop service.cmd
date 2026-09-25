@echo off
setlocal
"%~dp0resources\backend\ai-neko.exe" stop
set "AI_NEKO_STOP_EXIT=%ERRORLEVEL%"
pause
exit /b %AI_NEKO_STOP_EXIT%
