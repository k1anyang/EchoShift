<#
.SYNOPSIS
    (Re)generate the .cmd launchers.

.DESCRIPTION
    The launchers are tiny generated files, and they have gone missing more than
    once.  This turns restoring them into one command instead of archaeology.

    They are written ASCII-only on purpose: cmd.exe parses a .cmd with the OEM
    code page, so UTF-8 text in one would be misread as garbage commands on a
    zh-CN system.

    The CLI launcher must NOT be named audioconv.cmd / EchoShift-cli.cmd-like
    variants of the GUI one: Windows filenames are case-insensitive, so a name
    differing only in case collapses into the same file and silently replaces
    the GUI launcher.  tests/test_launchers.py guards against exactly that.

.EXAMPLE
    .\tools\make_launchers.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

$gui = @'
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
'@

$cli = @'
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
'@

foreach ($pair in @(@{ Name = "EchoShift.cmd"; Body = $gui },
                    @{ Name = "EchoShift-CLI.cmd"; Body = $cli })) {
    $target = Join-Path $Root $pair.Name
    Set-Content -Path $target -Value $pair.Body -Encoding ASCII -NoNewline
    Write-Host "wrote $target"
}

# Prove both landed as separate files rather than collapsing into one.
$found = Get-ChildItem $Root -Filter "EchoShift*.cmd" | Select-Object -ExpandProperty Name
Write-Host ""
Write-Host "launchers present: $($found -join ', ')"
if ($found.Count -ne 2) {
    throw "Expected 2 launchers, found $($found.Count). Name collision?"
}
