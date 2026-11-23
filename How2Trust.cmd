@echo off
setlocal DisableDelayedExpansion
set "repo=%~dp0examples\shop"
if not "%~1"=="" set "repo=%~f1"
set "PYTHONPATH=%~dp0;%PYTHONPATH%"
py -3 -c "import sys; sys.exit(sys.version_info < (3,10))" >nul 2>&1
if errorlevel 1 goto bundled
set "runtime=py"
set "py_version=-3"
goto launch
:bundled
set "runtime=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if not exist "%runtime%" set "runtime=python"
set "py_version="
:launch
if "%~1"=="--help" goto cli
if "%~1"=="-h" goto cli
if /i "%~1"=="review" goto cli
if /i "%~1"=="review-gui" goto cli
if /i "%~1"=="draft" goto cli
if /i "%~1"=="serve" goto cli
if /i "%~1"=="export" goto cli
"%runtime%" %py_version% -m how2trust serve "%repo%" --port 0 --open
goto finish
:cli
"%runtime%" %py_version% -m how2trust %*
:finish
set "result=%errorlevel%"
if not "%result%"=="0" pause
exit /b %result%
