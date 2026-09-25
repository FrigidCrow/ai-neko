@echo off
setlocal
echo Starting the M0 local foundation probe. This version has no chat screen.
"%~dp0ai-neko.exe" serve
set "AI_NEKO_START_EXIT=%ERRORLEVEL%"
echo.
echo Service exit code: %AI_NEKO_START_EXIT%
pause
exit /b %AI_NEKO_START_EXIT%
