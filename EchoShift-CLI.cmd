@echo off
rem EchoShift command line interface.
rem
rem   EchoShift-CLI.cmd <file or folder ...> [options]
rem   EchoShift-CLI.cmd --help
rem   EchoShift-CLI.cmd --list-presets
rem   EchoShift-CLI.cmd broken.mflac --diagnose
rem
rem Do NOT give this file a name that differs from EchoShift.cmd only in case:
rem Windows filenames are case-insensitive, so it would silently overwrite the
rem GUI launcher.  tests/test_launchers.py guards against that.
rem
rem Keep this file ASCII-only (see the note in EchoShift.cmd).
setlocal
set "PYTHONPATH=%~dp0src"
set "PYTHONIOENCODING=utf-8"

where python >nul 2>nul
if errorlevel 1 (
  echo python not found. Install Python 3.10+, or use dist\echoshift\echoshift.exe
  exit /b 1
)

python -m echoshift %*
exit /b %ERRORLEVEL%