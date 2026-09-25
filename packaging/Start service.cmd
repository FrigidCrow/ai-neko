@echo off
setlocal
echo Starting the backend diagnostic service. Use ai-neko.exe for the desktop pet.
"%~dp0resources\backend\ai-neko.exe" serve
set "AI_NEKO_START_EXIT=%ERRORLEVEL%"
echo.
echo Service exit code: %AI_NEKO_START_EXIT%
pause
exit /b %AI_NEKO_START_EXIT%
