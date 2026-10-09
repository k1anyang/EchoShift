<#
.SYNOPSIS
    Populate vendor/ffmpeg with an ffmpeg + ffprobe build that includes libmp3lame.

.DESCRIPTION
    Two modes:

    * -SourceDir <path>  copy from an ffmpeg installation already on this machine
                         (the directory holding ffmpeg.exe and ffprobe.exe).
    * (default)          download the BtbN "gpl-shared" Windows build and extract
                         ffmpeg.exe, ffprobe.exe and the DLLs they need.

    Either way the result is verified by asking ffmpeg for its encoder list, so a
    build without libmp3lame is rejected instead of shipped.

.EXAMPLE
    .\tools\vendor_ffmpeg.ps1
    .\tools\vendor_ffmpeg.ps1 -SourceDir "C:\ffmpeg\bin"
#>
[CmdletBinding()]
param(
    [string]$SourceDir,
    [string]$Url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl-shared.zip",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Target = Join-Path $Root "vendor\ffmpeg"
$ExeNames = @("ffmpeg.exe", "ffprobe.exe")

function Assert-Libmp3lame {
    param([string]$Directory)
    $ffmpeg = Join-Path $Directory "ffmpeg.exe"
    $encoders = & $ffmpeg -hide_banner -encoders 2>&1 | Out-String
    if ($encoders -notmatch "libmp3lame") {
        throw "该 ffmpeg 不包含 libmp3lame，无法编码 MP3：$ffmpeg"
    }
    $version = (& $ffmpeg -hide_banner -version 2>&1 | Select-Object -First 1)
    Write-Host "  ✓ $version"
}

if ((Test-Path (Join-Path $Target "ffmpeg.exe")) -and -not $Force) {
    Write-Host "vendor\ffmpeg 已存在，如需重建请加 -Force。"
    Assert-Libmp3lame -Directory $Target
    exit 0
}

New-Item -ItemType Directory -Force -Path $Target | Out-Null
Get-ChildItem $Target -File -ErrorAction SilentlyContinue | Remove-Item -Force

if ($SourceDir) {
    Write-Host "从 $SourceDir 复制 …"
    if (-not (Test-Path $SourceDir)) { throw "目录不存在：$SourceDir" }
    foreach ($name in $ExeNames) {
        $source = Join-Path $SourceDir $name
        if (-not (Test-Path $source)) { throw "缺少 $name（$SourceDir）" }
        Copy-Item $source $Target -Force
    }
    # A shared build needs its DLLs sitting next to the binaries.
    Get-ChildItem $SourceDir -Filter *.dll -ErrorAction SilentlyContinue |
        Copy-Item -Destination $Target -Force
}
else {
    Write-Host "下载 $Url …"
    $staging = Join-Path $env:TEMP ("echoshift_ffmpeg_" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Force -Path $staging | Out-Null
    try {
        $archive = Join-Path $staging "ffmpeg.zip"
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $Url -OutFile $archive -UseBasicParsing
        Write-Host "解压 …"
        Expand-Archive -Path $archive -DestinationPath (Join-Path $staging "x") -Force

        $binDir = Get-ChildItem (Join-Path $staging "x") -Recurse -Directory |
            Where-Object { $_.Name -eq "bin" } | Select-Object -First 1
        if (-not $binDir) { throw "压缩包里找不到 bin 目录" }

        foreach ($name in $ExeNames) {
            $source = Join-Path $binDir.FullName $name
            if (-not (Test-Path $source)) { throw "缺少 $name" }
            Copy-Item $source $Target -Force
        }
        Get-ChildItem $binDir.FullName -Filter *.dll | Copy-Item -Destination $Target -Force
    }
    finally {
        Remove-Item $staging -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "校验 …"
Assert-Libmp3lame -Directory $Target
$size = [math]::Round(((Get-ChildItem $Target -File | Measure-Object Length -Sum).Sum / 1MB), 1)
Write-Host "完成：$Target（$size MB）"
