@echo off
setlocal DisableDelayedExpansion
set "PYTHONUTF8=1"
set "PYEXE=py"
set "PYARG=-3"
if exist "%~dp0venv\Scripts\python.exe" set "PYEXE=%~dp0venv\Scripts\python.exe"
if exist "%~dp0venv\Scripts\python.exe" set "PYARG="
if exist "%~dp0.venv\Scripts\python.exe" set "PYEXE=%~dp0.venv\Scripts\python.exe"
if exist "%~dp0.venv\Scripts\python.exe" set "PYARG="
set "ESP_HOST=%~1"
if not defined ESP_HOST exit /b 2
"%PYEXE%" %PYARG% "%~dp0wifidebug.py" uart --host "%ESP_HOST%" --stdio
exit /b %ERRORLEVEL%
