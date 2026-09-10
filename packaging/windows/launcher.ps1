# 转录小工具 · Windows 本机助手
# 准备环境并拉起本地服务。不打开 127.0.0.1，准备好后回到原来的网页继续。
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Data = Join-Path $env:LOCALAPPDATA "media-transcriber"
$LogDir = Join-Path $Data "logs"
$Log = Join-Path $LogDir "media-transcriber.log"
$PidFile = Join-Path $Data "server.pid"
$Port = if ($env:MT_PORT) { $env:MT_PORT } else { "8766" }
$Local = "http://127.0.0.1:$Port"
$Repo = "hotcatty/media-transcriber"

New-Item -ItemType Directory -Force -Path (Join-Path $Data "bin"), (Join-Path $Data "app"), $LogDir | Out-Null

function Write-Log([string]$Message) {
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -Path $Log -Value $line -Encoding UTF8
}

function Show-Alert([string]$Message) {
    Add-Type -AssemblyName System.Windows.Forms | Out-Null
    [System.Windows.Forms.MessageBox]::Show($Message, "转录小工具") | Out-Null
}

function Copy-Diagnostics([string]$Message) {
    $lines = @(
        "转录小工具 诊断",
        "time=$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')",
        "launcher=windows-20260910e",
        "os=$([Environment]::OSVersion.VersionString)",
        "root=$Root",
        "error=$Message",
        "---- log ----"
    )
    if (Test-Path $Log) {
        $lines += Get-Content $Log -Tail 160 -ErrorAction SilentlyContinue |
            Where-Object { $_ -notmatch '(?i)cookie|authorization|token=|password' }
    } else {
        $lines += "(no log)"
    }
    $text = $lines -join "`r`n"
    $diag = Join-Path $LogDir "media-transcriber-diag.txt"
    Set-Content -Path $diag -Value $text -Encoding UTF8
    try {
        Set-Clipboard -Value $text
    } catch { }
}

function Fail([string]$Message) {
    Write-Log "ERROR: $Message"
    Copy-Diagnostics $Message
    Add-Type -AssemblyName System.Windows.Forms | Out-Null
    $r = [System.Windows.Forms.MessageBox]::Show(
        "本机组件没准备好。$Message`r`n`r`n说明已复制。回到聊天粘贴发给我即可。",
        "转录小工具",
        [System.Windows.Forms.MessageBoxButtons]::OK
    )
    exit 1
}

function Test-HelperReady {
    try {
        $resp = Invoke-WebRequest -Uri "$Local/api/ping" -UseBasicParsing -TimeoutSec 1
        $body = $resp.Content
        if ($body -notmatch '"app"\s*:\s*"media-transcriber"') { return $false }
        if ($body -match '"preparing"\s*:\s*true') { return $false }
        return $true
    } catch {
        return $false
    }
}

function Sync-AppDir([string]$From, [string]$To) {
    New-Item -ItemType Directory -Force -Path $To | Out-Null
    $code = 0
    & robocopy $From $To /MIR /XD temp .venv /XF cookies.txt /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
    $code = $LASTEXITCODE
    if ($code -ge 8) { Fail "没法复制程序文件。" }
}

function Register-Protocol {
    $reg = "HKCU:\Software\Classes\media-transcriber"
    New-Item -Path $reg -Force | Out-Null
    Set-ItemProperty -Path $reg -Name "(default)" -Value "URL:media-transcriber"
    New-ItemProperty -Path $reg -Name "URL Protocol" -Value "" -PropertyType String -Force | Out-Null
    $cmdKey = Join-Path $reg "shell\open\command"
    New-Item -Path $cmdKey -Force | Out-Null
    $ps = Join-Path $env:WINDIR "System32\WindowsPowerShell\v1.0\powershell.exe"
    $script = Join-Path $Root "launcher.ps1"
    $command = "`"$ps`" -NoProfile -ExecutionPolicy Bypass -File `"$script`" `"%1`""
    Set-ItemProperty -Path $cmdKey -Name "(default)" -Value $command
}

function Fetch-Url([string]$Dest, [string[]]$Urls) {
    foreach ($url in $Urls) {
        if ([string]::IsNullOrWhiteSpace($url)) { continue }
        Write-Log "fetch $url"
        try {
            Invoke-WebRequest -Uri $url -OutFile $Dest -UseBasicParsing -TimeoutSec 180
            if (Test-Path $Dest) { return $true }
        } catch {
            if (Test-Path $Dest) { Remove-Item $Dest -Force }
        }
    }
    return $false
}

function GitHub-Fetch([string]$Dest, [string]$Url) {
    return Fetch-Url $Dest @(
        $Url,
        "https://ghfast.top/$Url",
        "https://ghproxy.net/$Url"
    )
}

if (Test-HelperReady) {
    Write-Log "already running"
    exit 0
}

$env:MT_HOST = "127.0.0.1"
$env:MT_PORT = "$Port"
$env:MT_TEMP_DIR = Join-Path $Data "temp"
$env:MT_COOKIE_FILE = Join-Path $Data "cookies.txt"
$env:MT_FFMPEG = Join-Path $Data "bin\ffmpeg.exe"
$env:MT_FFPROBE = Join-Path $Data "bin\ffprobe.exe"
if (-not $env:HF_ENDPOINT) { $env:HF_ENDPOINT = "https://hf-mirror.com" }
if (-not $env:UV_INDEX_URL) { $env:UV_INDEX_URL = "https://pypi.tuna.tsinghua.edu.cn/simple" }
if (-not $env:PIP_INDEX_URL) { $env:PIP_INDEX_URL = "https://pypi.tuna.tsinghua.edu.cn/simple" }
if (-not $env:UV_PYTHON_INSTALL_MIRROR) {
    $env:UV_PYTHON_INSTALL_MIRROR = "https://cdn.npmmirror.com/binaries/python-build-standalone"
}
$bundledPython = Join-Path $Root "python"
$hasBundledPython = (Test-Path $bundledPython) -and (@(Get-ChildItem $bundledPython -Recurse -File -ErrorAction SilentlyContinue).Count -gt 0)
if ($hasBundledPython) {
    $env:UV_PYTHON_INSTALL_DIR = $bundledPython
} else {
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $Data "python"
}
$env:Path = "$(Join-Path $Data 'bin');$(Join-Path $Root 'bin');$env:Path"

Write-Log "launch begin"
Register-Protocol

function Ensure-Uv {
    $dest = Join-Path $Data "bin\uv.exe"
    $bundled = Join-Path $Root "bin\uv.exe"
    if (Test-Path $bundled) {
        Copy-Item $bundled $dest -Force
        Unblock-File -Path $dest -ErrorAction SilentlyContinue
        return
    }
    if (Test-Path $dest) { return }
    $zip = Join-Path $Data "uv.zip"
    $ok = GitHub-Fetch $zip "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip"
    if (-not $ok) { Fail "没法下载运行环境。请检查网络后再打开一次。" }
    Expand-Archive -Path $zip -DestinationPath (Join-Path $Data "bin") -Force
    Remove-Item $zip -Force -ErrorAction SilentlyContinue
    if (-not (Test-Path $dest)) {
        $found = Get-ChildItem (Join-Path $Data "bin") -Recurse -Filter uv.exe | Select-Object -First 1
        if ($found) { Copy-Item $found.FullName $dest -Force }
    }
    if (-not (Test-Path $dest)) { Fail "运行环境下载后无法使用。" }
}

function Ensure-Ffmpeg {
    $ff = Join-Path $Data "bin\ffmpeg.exe"
    $fp = Join-Path $Data "bin\ffprobe.exe"
    if ((Test-Path $ff) -and (Test-Path $fp)) { return }
    $bff = Join-Path $Root "bin\ffmpeg.exe"
    $bfp = Join-Path $Root "bin\ffprobe.exe"
    if ((Test-Path $bff) -and (Test-Path $bfp)) {
        Copy-Item $bff $ff -Force
        Copy-Item $bfp $fp -Force
        return
    }
    $tag = "b6.1.1"
    $ffgz = Join-Path $Data "bin\ffmpeg.gz"
    $fpgz = Join-Path $Data "bin\ffprobe.gz"
    if (-not (GitHub-Fetch $ffgz "https://github.com/eugeneware/ffmpeg-static/releases/download/$tag/ffmpeg-win32-x64.gz")) {
        Fail "没法下载音频组件。请检查网络后再打开一次。"
    }
    if (-not (GitHub-Fetch $fpgz "https://github.com/eugeneware/ffmpeg-static/releases/download/$tag/ffprobe-win32-x64.gz")) {
        Fail "没法下载音频组件。请检查网络后再打开一次。"
    }
    Add-Type -AssemblyName System.IO.Compression | Out-Null
    foreach ($pair in @(@($ffgz, $ff), @($fpgz, $fp))) {
        $fs = [System.IO.File]::OpenRead($pair[0])
        $gz = New-Object System.IO.Compression.GzipStream($fs, [System.IO.Compression.CompressionMode]::Decompress)
        $out = [System.IO.File]::Create($pair[1])
        $gz.CopyTo($out)
        $out.Close(); $gz.Close(); $fs.Close()
    }
    Remove-Item $ffgz, $fpgz -Force -ErrorAction SilentlyContinue
}

function Copy-BundledApp {
    $src = Join-Path $Root "app"
    if (Test-Path (Join-Path $src "backend")) {
        Sync-AppDir $src (Join-Path $Data "app")
        return $true
    }
    return $false
}

function Refresh-App {
    if (-not (Test-Path (Join-Path $Data "app\backend"))) {
        [void](Copy-BundledApp)
        Write-Log "using bundled app"
    }
    $zip = Join-Path $Data "src.zip"
    $tmp = Join-Path $Data "src-unpack"
    if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null
    $ok = GitHub-Fetch $zip "https://codeload.github.com/$Repo/zip/refs/heads/main"
    if (-not $ok) { $ok = Fetch-Url $zip @("https://api.github.com/repos/$Repo/zipball/main") }
    if ($ok) {
        Expand-Archive -Path $zip -DestinationPath $tmp -Force
        $inner = Get-ChildItem $tmp -Directory | Select-Object -First 1
        if ($inner -and (Test-Path (Join-Path $inner.FullName "backend"))) {
            Sync-AppDir $inner.FullName (Join-Path $Data "app")
            Remove-Item $tmp, $zip -Recurse -Force -ErrorAction SilentlyContinue
            return
        }
    }
    Remove-Item $tmp, $zip -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path (Join-Path $Data "app\backend")) {
        Write-Log "keep existing app copy"
        return
    }
    if (Copy-BundledApp) {
        Write-Log "using bundled app"
        return
    }
    Fail "没法下载程序文件。请检查网络后再打开一次。"
}

function Find-BundledPython {
    $root = Join-Path $Root "python"
    if (-not (Test-Path $root)) { return $null }
    $found = Get-ChildItem $root -Recurse -Filter python.exe -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -notmatch '\\Lib\\' -and $_.FullName -notmatch '\\Scripts\\' } |
        Select-Object -First 1
    if ($found) { return $found.FullName }
    return $null
}

function Ensure-Venv {
    $uv = Join-Path $Data "bin\uv.exe"
    $py = Join-Path $Data "venv\Scripts\python.exe"
    if ((Test-Path (Join-Path $Data "venv")) -and -not (Test-Path $py)) {
        Remove-Item (Join-Path $Data "venv") -Recurse -Force -ErrorAction SilentlyContinue
    }
    if (-not (Test-Path $py)) {
        Write-Log "create venv"
        $bundledPy = Find-BundledPython
        if ($bundledPy) {
            Write-Log "using bundled python $bundledPy"
            Unblock-File -Path $bundledPy -ErrorAction SilentlyContinue
            & $uv venv (Join-Path $Data "venv") --python $bundledPy
            if ($LASTEXITCODE -ne 0 -or -not (Test-Path $py)) {
                & $bundledPy -m venv (Join-Path $Data "venv")
            }
            if (-not (Test-Path $py)) { Fail "没法创建运行环境。" }
        } else {
            Write-Log "no bundled python, try uv install"
            $ok = $false
            foreach ($ver in @("3.12", "3.11")) {
                & $uv python install $ver
                if ($LASTEXITCODE -ne 0) { continue }
                & $uv venv (Join-Path $Data "venv") --python $ver
                if ($LASTEXITCODE -eq 0 -and (Test-Path $py)) {
                    $ok = $true
                    break
                }
            }
            if (-not $ok) { Fail "没法准备 Python。请删掉旧文件后重新下载再打开。" }
        }
    }
    Write-Log "install python packages"
    & $uv pip install --python $py -r (Join-Path $Data "app\requirements.txt")
    if ($LASTEXITCODE -ne 0) { Fail "没法安装依赖。请检查网络后再打开一次。" }
}

function Start-Server {
    if (Test-HelperReady) { return }
    $py = Join-Path $Data "venv\Scripts\python.exe"
    $app = Join-Path $Data "app"
    Write-Log "start server"
    $proc = Start-Process -FilePath $py -ArgumentList "start.py","--prod" -WorkingDirectory $app -WindowStyle Hidden -PassThru
    Set-Content -Path $PidFile -Value $proc.Id
}

function Wait-Ready {
    for ($i = 0; $i -lt 90; $i++) {
        if (Test-HelperReady) { return }
        Start-Sleep -Seconds 1
    }
    Fail "服务启动超时。关掉再打开一次试试。"
}

try {
    Ensure-Uv
    Refresh-App
    Ensure-Venv
    Ensure-Ffmpeg
    Start-Server
    Wait-Ready
    Write-Log "launch ok"
} catch {
    Fail "没法安装运行环境。请检查网络后再打开一次。"
}
exit 0
