@echo off
setlocal DisableDelayedExpansion
set "PYTHONUTF8=1"
set "PYEXE=py"
set "PYARG=-3"
if exist "%~dp0venv\Scripts\python.exe" set "PYEXE=%~dp0venv\Scripts\python.exe"
if exist "%~dp0venv\Scripts\python.exe" set "PYARG="
if exist "%~dp0.venv\Scripts\python.exe" set "PYEXE=%~dp0.venv\Scripts\python.exe"
if exist "%~dp0.venv\Scripts\python.exe" set "PYARG="
1>&2 echo STM32 persistent framed UART. Ctrl-C stops. No reset on open.
1>&2 echo Programming pauses and resumes this connection; paused input is discarded.
1>&2 echo Token: STM32_ROM_TOKEN environment or companion token.txt.
set "ESP_HOST=%~1"
if not defined ESP_HOST set /p "ESP_HOST=ESP IP: "
if not defined ESP_HOST exit /b 2
"%PYEXE%" %PYARG% "%~dp0wifidebug.py" uart --host "%ESP_HOST%" --baud 115200
set "RESULT=%ERRORLEVEL%"
1>&2 echo Client exit code: %RESULT%
1>&2 pause
exit /b %RESULT%
