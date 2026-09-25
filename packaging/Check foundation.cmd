@echo off
setlocal
"%~dp0ai-neko.exe" self-check
set "AI_NEKO_CHECK_EXIT=%ERRORLEVEL%"
echo.
echo M0 foundation check exit code: %AI_NEKO_CHECK_EXIT%
pause
exit /b %AI_NEKO_CHECK_EXIT%
