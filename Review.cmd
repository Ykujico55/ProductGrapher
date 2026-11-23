@echo off
setlocal DisableDelayedExpansion
if "%~1"=="" (
    call "%~dp0How2Trust.cmd" review-gui
) else (
    call "%~dp0How2Trust.cmd" review-gui "%~f1"
)
exit /b %errorlevel%
