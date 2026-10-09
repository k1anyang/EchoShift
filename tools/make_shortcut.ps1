<#
.SYNOPSIS
    Create a shortcut that launches the EchoShift GUI with no console window.

.DESCRIPTION
    The shortcut targets pythonw.exe directly, bypassing cmd.exe entirely, and
    hands it launch_gui.pyw -- which puts src\ on sys.path itself, so the
    shortcut needs no environment variables and survives the folder being moved
    (re-run this script afterwards).

    Keep this file ASCII-only: Windows PowerShell 5.1 reads a .ps1 without a BOM
    as ANSI, so UTF-8 text here would be mangled on a zh-CN system.

.PARAMETER Destination
    Folder to create the shortcut in.  Defaults to the project root.

.PARAMETER Name
    Shortcut base name, without the .lnk extension.

.EXAMPLE
    .\tools\make_shortcut.ps1
    .\tools\make_shortcut.ps1 -Destination "$env:USERPROFILE\Desktop"
#>
[CmdletBinding()]
param(
    [string]$Destination,
    [string]$Name = "EchoShift"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (-not $Destination) { $Destination = $Root }

$launcher = Join-Path $Root "launch_gui.pyw"
$icon = Join-Path $Root "assets\echoshift.ico"

if (-not (Test-Path $launcher)) { throw "Missing launcher: $launcher" }

$pythonw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue)
if (-not $pythonw) { throw "pythonw.exe not found on PATH. Install Python 3.10+ first." }

if (-not (Test-Path $Destination)) { New-Item -ItemType Directory -Force -Path $Destination | Out-Null }
$target = Join-Path $Destination "$Name.lnk"

$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($target)
$link.TargetPath = $pythonw.Source
$link.Arguments = '"' + $launcher + '"'
$link.WorkingDirectory = $Root
$link.Description = "EchoShift - audio to MP3"
if (Test-Path $icon) { $link.IconLocation = "$icon,0" }
$link.WindowStyle = 1
$link.Save()

Write-Host "Shortcut created: $target"
Write-Host "  target : $($pythonw.Source)"
Write-Host "  args   : `"$launcher`""
Write-Host "  workdir: $Root"
if (Test-Path $icon) { Write-Host "  icon   : $icon" }

# Verify what actually landed in the .lnk rather than trusting the write.
$check = $shell.CreateShortcut($target)
Write-Host ""
Write-Host "Verification:"
Write-Host "  TargetPath       : $($check.TargetPath)"
Write-Host "  Arguments        : $($check.Arguments)"
Write-Host "  WorkingDirectory : $($check.WorkingDirectory)"
