@echo off
rem EchoShift - launch the graphical interface (runs from source, no build needed).
rem Keep this file ASCII-only: cmd.exe parses .cmd bytes with the OEM code page,
rem so UTF-8 Chinese text here would be misinterpreted on a zh-CN system.
setlocal
set "ROOT=%~dp0"
set "PYTHONPATH=%ROOT%src"

where pythonw >nul 2>nul
if errorlevel 1 (
  echo.
  echo   pythonw not found.
  echo   Install Python 3.10+ and tick "Add python.exe to PATH",
  echo   or run the packaged build: dist\EchoShift\EchoShift.exe
  echo.
  pause
  exit /b 1
)

start "" pythonw -m echoshift.gui.app
endlocal