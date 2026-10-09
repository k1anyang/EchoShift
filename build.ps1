<#
.SYNOPSIS
    Build the EchoShift executable with PyInstaller.

.DESCRIPTION
    Produces the windowed GUI build ``dist\EchoShift\EchoShift.exe``.  The
    vendored ffmpeg is bundled, so the result runs on a machine with no Python
    and no ffmpeg installed.

    A folder layout is the default: the bundled ffmpeg DLLs are ~50 MB and a
    one-file build would re-extract all of them on every launch.

.PARAMETER OneFile
    Produce a self-extracting single executable instead of a folder.

.PARAMETER Python
    Python executable used for tests and PyInstaller.

.PARAMETER SkipTests
    Do not run the pytest suite first.

.EXAMPLE
    .\build.ps1
    .\build.ps1 -OneFile -SkipTests
#>
[CmdletBinding()]
param(
    [switch]$OneFile,
    [switch]$SkipTests,
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

Write-Host "== 环境检查 ==" -ForegroundColor Cyan

$pythonCommand = (Get-Command $Python -ErrorAction SilentlyContinue)
if (-not $pythonCommand) { throw "找不到 Python：$Python" }
Write-Host "  python: $(& $Python --version)"

$PyInstallerCommand = $Python
$PyInstallerArgs = @("-m", "PyInstaller")
try {
    & $Python -c "import PyInstaller" 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw }
}
catch {
    $appData = if ($env:APPDATA) { $env:APPDATA } else { "" }
    $fallbacks = @(
        (Get-Command pyinstaller.exe -ErrorAction SilentlyContinue).Source,
        (Join-Path $appData "Python\Python313\Scripts\pyinstaller.exe")
    ) | Where-Object { $_ -and (Test-Path $_) }
    if (-not $fallbacks) {
        throw "缺少 PyInstaller，请先运行：python -m pip install pyinstaller"
    }
    $PyInstallerCommand = $fallbacks[0]
    $PyInstallerArgs = @()
}
$pyiVersion = (& $PyInstallerCommand @PyInstallerArgs --version 2>&1 | Select-Object -Last 1)
Write-Host "  pyinstaller: $pyiVersion"

if (-not (Test-Path "vendor\ffmpeg\ffmpeg.exe")) {
    throw "vendor\ffmpeg 里没有 ffmpeg.exe。先运行：.\tools\vendor_ffmpeg.ps1"
}
$ffmpegEncoders = (& ".\vendor\ffmpeg\ffmpeg.exe" -hide_banner -encoders 2>&1 | Out-String)
if ($ffmpegEncoders -notmatch "libmp3lame") {
    throw "vendor\ffmpeg 的 ffmpeg 不含 libmp3lame，无法编码 MP3。"
}
Write-Host "  ffmpeg: 已内置且支持 libmp3lame"

if (-not $SkipTests) {
    Write-Host ""
    Write-Host "== 运行测试 ==" -ForegroundColor Cyan
    & $Python -m pytest tests -q
    if ($LASTEXITCODE -ne 0) { throw "测试未通过，已中止打包。" }
}

if ($OneFile) {
    $env:ECHOSHIFT_ONEFILE = "1"
    Write-Host ""
    Write-Host "模式：单文件（每次启动会解压内置 ffmpeg，较慢）" -ForegroundColor Yellow
}
else {
    Remove-Item Env:\ECHOSHIFT_ONEFILE -ErrorAction SilentlyContinue
    Write-Host ""
    Write-Host "模式：文件夹（推荐，启动快）" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "== 清理旧产物 ==" -ForegroundColor Cyan
foreach ($path in @("build", "dist")) {
    if (Test-Path $path) { Remove-Item $path -Recurse -Force }
}

Write-Host ""
Write-Host "== 打包 ==" -ForegroundColor Cyan
# PyInstaller writes its progress to stderr, which Windows PowerShell 5.1 turns
# into a terminating NativeCommandError while $ErrorActionPreference is Stop.
# Relax it for this one call and judge the result by the exit code instead.
$previous = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$pyiLog = & $PyInstallerCommand @PyInstallerArgs "packaging\EchoShift.spec" --noconfirm --distpath dist --workpath build 2>&1
$buildExit = $LASTEXITCODE
$ErrorActionPreference = $previous
# PyInstaller logs a line per bundled module.  Show only what could mean
# trouble, and keep the full log on disk for when it does fail.
$pyiLog | Set-Content -Path "build\pyinstaller.log" -Encoding UTF8
$pyiLog | Where-Object { $_ -match '^\d+ (WARNING|ERROR)|Traceback|ImportError' } |
    ForEach-Object { Write-Host $_ }
if ($buildExit -ne 0) {
    Write-Host "完整日志：build\pyinstaller.log" -ForegroundColor Yellow
    throw "打包失败：packaging\EchoShift.spec (exit $buildExit)"
}

if (-not (Test-Path "dist\EchoShift\EchoShift.exe")) {
    throw "打包结束但没找到 dist\EchoShift\EchoShift.exe"
}

Write-Host ""
Write-Host "== 产物 ==" -ForegroundColor Green
Get-ChildItem dist | ForEach-Object {
    if ($_.PSIsContainer) {
        $size = [math]::Round(((Get-ChildItem $_.FullName -Recurse -File |
            Measure-Object Length -Sum).Sum / 1MB), 1)
        Write-Host ("  {0}\  ({1} MB)" -f $_.Name, $size)
        Get-ChildItem $_.FullName -Filter *.exe | ForEach-Object {
            Write-Host ("      " + $_.Name)
        }
    }
    else {
        Write-Host ("  {0}  ({1} MB)" -f $_.Name, [math]::Round($_.Length / 1MB, 1))
    }
}

Write-Host ""
Write-Host "完成。可以直接把 dist 下的内容拷到别的 Windows 机器上运行。" -ForegroundColor Green


