# 猫听转文字 · Windows 本机应用
# 准备环境并打开桌面窗口。
$ErrorActionPreference = "Stop"
Write-Host "正在启动猫听转文字…"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Data = Join-Path $env:LOCALAPPDATA "media-transcriber"
$LogDir = Join-Path $Data "logs"
$Log = Join-Path $LogDir "media-transcriber.log"
$PidFile = Join-Path $Data "server.pid"
$Port = if ($env:MT_PORT) { $env:MT_PORT } else { "8766" }
$Local = "http://127.0.0.1:$Port"

New-Item -ItemType Directory -Force -Path (Join-Path $Data "bin"), (Join-Path $Data "app"), $LogDir | Out-Null

function Write-Log([string]$Message) {
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -Path $Log -Value $line -Encoding UTF8
}

function Show-Alert([string]$Message) {
    Add-Type -AssemblyName System.Windows.Forms | Out-Null
    [System.Windows.Forms.MessageBox]::Show($Message, "猫听转文字") | Out-Null
}

function Copy-Diagnostics([string]$Message) {
    $lines = @(
        "猫听转文字 诊断",
        "time=$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')",
        "launcher=windows-20261113a",
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
    $downloads = Join-Path ([Environment]::GetFolderPath("UserProfile")) "Downloads"
    if (-not (Test-Path $downloads)) {
        New-Item -ItemType Directory -Force -Path $downloads | Out-Null
    }
    $dl = Join-Path $downloads "猫听转文字-说明.txt"
    Set-Content -Path $dl -Value $text -Encoding UTF8
    try {
        Set-Clipboard -Value $text
    } catch { }
}

function Show-DiagWindow([string]$Text) {
    Add-Type -AssemblyName System.Windows.Forms | Out-Null
    Add-Type -AssemblyName System.Drawing | Out-Null
    $form = New-Object System.Windows.Forms.Form
    $form.Text = "猫听转文字 · 说明"
    $form.Width = 720
    $form.Height = 560
    $form.StartPosition = "CenterScreen"
    $panel = New-Object System.Windows.Forms.Panel
    $panel.Dock = "Bottom"
    $panel.Height = 44
    $save = New-Object System.Windows.Forms.Button
    $save.Text = "下载说明.txt"
    $save.Width = 140
    $save.Height = 32
    $save.Left = 8
    $save.Top = 6
    $btn = New-Object System.Windows.Forms.Button
    $btn.Text = "复制说明"
    $btn.Width = 120
    $btn.Height = 32
    $btn.Left = 156
    $btn.Top = 6
    $box = New-Object System.Windows.Forms.TextBox
    $box.Multiline = $true
    $box.ScrollBars = "Both"
    $box.ReadOnly = $true
    $box.Dock = "Fill"
    $box.Font = New-Object System.Drawing.Font("Consolas", 10)
    $box.WordWrap = $false
    $box.Text = $Text
    $btn.Tag = $box
    $save.Tag = $box
    $btn.Add_Click({
        $src = $this.Tag
        Set-Clipboard -Value $src.Text
        $this.Text = "已复制"
    })
    $save.Add_Click({
        $src = $this.Tag
        $dlg = New-Object System.Windows.Forms.SaveFileDialog
        $dlg.Filter = "Text (*.txt)|*.txt"
        $dlg.FileName = "猫听转文字-说明.txt"
        $dlg.InitialDirectory = Join-Path ([Environment]::GetFolderPath("UserProfile")) "Downloads"
        if ($dlg.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
            [IO.File]::WriteAllText($dlg.FileName, $src.Text, [Text.UTF8Encoding]::new($false))
            $this.Text = "已保存"
        }
    })
    $panel.Controls.Add($save)
    $panel.Controls.Add($btn)
    $form.Controls.Add($box)
    $form.Controls.Add($panel)
    [void]$form.ShowDialog()
}

function Fail([string]$Message) {
    Write-Host ""
    Write-Host "启动失败：$Message"
    try { Write-Log "ERROR: $Message" } catch {}
    try { Stop-Bootstrap } catch {}
    try { Copy-Diagnostics $Message } catch {}
    try {
        $diag = Join-Path $LogDir "media-transcriber-diag.txt"
        $text = if (Test-Path $diag) { Get-Content $diag -Raw -Encoding UTF8 } else { $Message }
        Show-DiagWindow $text
    } catch {
        Write-Host "没法弹出说明窗口。"
    }
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

function Test-ServerPidAlive {
    if (-not (Test-Path $PidFile)) { return $false }
    $p = Get-Content $PidFile -ErrorAction SilentlyContinue
    if (-not $p) { return $false }
    return $null -ne (Get-Process -Id ([int]$p) -ErrorAction SilentlyContinue)
}

function Start-Bootstrap {
    if (Test-HelperReady) { return }
    $htmlPath = Join-Path $Root "waiting.html"
    if (-not (Test-Path $htmlPath)) { return }
    try {
        $script:BootListener = New-Object System.Net.HttpListener
        $script:BootListener.Prefixes.Add("$Local/")
        $script:BootListener.Start()
    } catch {
        Write-Log "bootstrap bind failed"
        $script:BootListener = $null
        return
    }
    $script:BootRunspace = [runspacefactory]::CreateRunspace()
    $script:BootRunspace.Open()
    $script:BootPS = [powershell]::Create()
    $script:BootPS.Runspace = $script:BootRunspace
    [void]$script:BootPS.AddScript({
        param($Listener, $HtmlPath)
        $html = [IO.File]::ReadAllBytes($HtmlPath)
        $ping = [Text.Encoding]::UTF8.GetBytes('{"ok":true,"app":"media-transcriber","preparing":true}')
        try {
            while ($Listener.IsListening) {
                $ctx = $Listener.GetContext()
                $path = $ctx.Request.Url.AbsolutePath
                $res = $ctx.Response
                try {
                    $origin = $ctx.Request.Headers["Origin"]
                    if ([string]::IsNullOrWhiteSpace($origin)) { $origin = "*" }
                    $res.Headers.Add("Access-Control-Allow-Origin", $origin)
                    $res.Headers.Add("Access-Control-Allow-Private-Network", "true")
                    if ($path -eq "/api/ping") {
                        $res.ContentType = "application/json; charset=utf-8"
                        $bytes = $ping
                    } else {
                        $res.ContentType = "text/html; charset=utf-8"
                        $bytes = $html
                    }
                    $res.ContentLength64 = $bytes.Length
                    $res.OutputStream.Write($bytes, 0, $bytes.Length)
                } catch {
                } finally {
                    try { $res.Close() } catch {}
                }
            }
        } catch {}
    }).AddArgument($script:BootListener).AddArgument($htmlPath)
    $script:BootAsync = $script:BootPS.BeginInvoke()
}

function Stop-Bootstrap {
    if ($script:BootListener) {
        try { $script:BootListener.Stop() } catch {}
        try { $script:BootListener.Close() } catch {}
        $script:BootListener = $null
    }
    if ($script:BootPS) {
        try { $script:BootPS.Stop() } catch {}
        try { $script:BootPS.Dispose() } catch {}
        $script:BootPS = $null
    }
    if ($script:BootRunspace) {
        try { $script:BootRunspace.Dispose() } catch {}
        $script:BootRunspace = $null
    }
}

function Stop-Splash {
    $splashPidFile = Join-Path $Data "splash.pid"
    if (-not (Test-Path $splashPidFile)) { return }
    $p = Get-Content $splashPidFile -ErrorAction SilentlyContinue
    Remove-Item $splashPidFile -Force -ErrorAction SilentlyContinue
    if ($p) {
        try { Stop-Process -Id ([int]$p) -Force -ErrorAction SilentlyContinue } catch {}
    }
}

function Stop-DesktopWindow {
    $pidFile = Join-Path $Data "window.pid"
    if (-not (Test-Path $pidFile)) { return }
    $p = Get-Content $pidFile -ErrorAction SilentlyContinue
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
    if ($p) {
        try { Stop-Process -Id ([int]$p) -Force -ErrorAction SilentlyContinue } catch {}
    }
}

function Open-DesktopWindow {
    $py = Join-Path $Data "venv\Scripts\python.exe"
    $script = Join-Path $Data "app\desktop_window.py"
    if (-not (Test-Path $script)) {
        $script = Join-Path $Root "app\desktop_window.py"
    }
    if ((Test-Path $py) -and (Test-Path $script)) {
        if (-not (Test-HelperReady) -and -not (Test-ServerPidAlive)) {
            Start-Bootstrap
            for ($i = 0; $i -lt 10; $i++) {
                try {
                    Invoke-WebRequest -Uri "$Local/api/ping" -UseBasicParsing -TimeoutSec 1 | Out-Null
                    break
                } catch {
                    Start-Sleep -Milliseconds 200
                }
            }
        }
        Write-Log "open desktop window"
        $proc = Start-Process -FilePath $py -ArgumentList @($script) -WorkingDirectory (Split-Path $script) -PassThru
        Set-Content -Path (Join-Path $Data "window.pid") -Value $proc.Id
        return
    }
    $splash = Join-Path $Root "desktop_splash.py"
    if (-not (Test-Path $splash)) { $splash = Join-Path $Data "app\desktop_splash.py" }
    if (Test-Path $splash) {
        $spy = Get-Command python3 -ErrorAction SilentlyContinue
        if (-not $spy) { $spy = Get-Command python -ErrorAction SilentlyContinue }
        $pyPath = if ($spy) { [string]$spy.Source } else { "" }
        # Windows 商店的 python.exe 是 0 字节占位，跑不了准备页，不能把它当成已安装。
        $storeAlias = $pyPath -match '\\WindowsApps\\'
        if ($pyPath -and -not $storeAlias) {
            try {
                Write-Log "open splash"
                $proc = Start-Process -FilePath $pyPath -ArgumentList @($splash) -PassThru -ErrorAction Stop
                Set-Content -Path (Join-Path $Data "splash.pid") -Value $proc.Id
                return
            } catch {
                Write-Log "splash failed"
            }
        }
    }
    if (-not (Test-HelperReady) -and -not (Test-ServerPidAlive)) {
        Start-Bootstrap
        for ($i = 0; $i -lt 10; $i++) {
            try {
                Invoke-WebRequest -Uri "$Local/api/ping" -UseBasicParsing -TimeoutSec 1 | Out-Null
                break
            } catch {
                Start-Sleep -Milliseconds 200
            }
        }
    }
    Write-Host "正在打开准备页面…"
    try { Start-Process $Local } catch { Write-Log "open waiting page failed" }
}

function Wait-DesktopWindow {
    $pidFile = Join-Path $Data "window.pid"
    if (-not (Test-Path $pidFile)) { return }
    $p = Get-Content $pidFile -ErrorAction SilentlyContinue
    if (-not $p) { return }
    try { Wait-Process -Id ([int]$p) -ErrorAction SilentlyContinue } catch {}
}

function Sync-AppDir([string]$From, [string]$To) {
    New-Item -ItemType Directory -Force -Path $To | Out-Null
    $code = 0
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & robocopy $From $To /MIR /XD temp .venv /XF cookies.txt /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
    $code = $LASTEXITCODE
    $ErrorActionPreference = $prevEap
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

$env:MT_HOST = "127.0.0.1"
$env:MT_PORT = "$Port"
$env:MT_TEMP_DIR = Join-Path $Data "temp"
$env:MT_AUTO_UPDATE = "1"
$env:MT_COOKIE_FILE = Join-Path $Data "cookies.txt"
$env:MT_FFMPEG = Join-Path $Data "bin\ffmpeg.exe"
$env:MT_FFPROBE = Join-Path $Data "bin\ffprobe.exe"
if (-not $env:HF_ENDPOINT) { $env:HF_ENDPOINT = "https://hf-mirror.com" }
Remove-Item Env:UV_INDEX_URL -ErrorAction SilentlyContinue
Remove-Item Env:PIP_INDEX_URL -ErrorAction SilentlyContinue
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
try {
    Register-Protocol
} catch {
    Write-Log "protocol skipped: $($_.Exception.Message)"
}

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

if (Test-HelperReady) {
    Write-Log "already running; reopen window"
    Stop-DesktopWindow
    Open-DesktopWindow
    Wait-DesktopWindow
    exit 0
}

function Get-AppVersion([string]$ConfigPath) {
    if (-not (Test-Path $ConfigPath)) { return "" }
    foreach ($line in (Get-Content $ConfigPath -ErrorAction SilentlyContinue)) {
        if ($line -match 'APP_VERSION = "([^"]+)"') { return $Matches[1] }
    }
    return ""
}

function Test-VersionGt([string]$Left, [string]$Right) {
    if ([string]::IsNullOrWhiteSpace($Left) -or [string]::IsNullOrWhiteSpace($Right)) { return $false }
    $a = $Left.Split(".")
    $b = $Right.Split(".")
    $n = [Math]::Max($a.Count, $b.Count)
    for ($i = 0; $i -lt $n; $i++) {
        $x = 0
        $y = 0
        if ($i -lt $a.Count) { [void][int]::TryParse(($a[$i] -replace '\D', ''), [ref]$x) }
        if ($i -lt $b.Count) { [void][int]::TryParse(($b[$i] -replace '\D', ''), [ref]$y) }
        if ($x -gt $y) { return $true }
        if ($x -lt $y) { return $false }
    }
    return $false
}

function Use-LocalApp {
    $installed = Join-Path $Data "app\backend"
    $bundledCfg = Join-Path $Root "app\backend\config.py"
    $installedCfg = Join-Path $Data "app\backend\config.py"
    if (Test-Path $installed) {
        $bv = Get-AppVersion $bundledCfg
        $iv = Get-AppVersion $installedCfg
        if (Test-VersionGt $bv $iv) {
            Write-Log "copy newer bundled app $bv over $iv"
            try { [void](Copy-BundledApp) } catch { Write-Log "bundle copy failed; keep installed" }
        } else {
            Write-Log "using installed app $iv"
        }
        return
    }
    if (Test-Path (Join-Path $Data "pending-update\tree\backend")) {
        Write-Log "no installed app yet; staged update will supply it"
        New-Item -ItemType Directory -Force -Path (Join-Path $Data "app") | Out-Null
        return
    }
    if (Copy-BundledApp) {
        Write-Log "using bundled app"
        return
    }
    Fail "找不到已经装好的程序。请重新安装。"
}

function Apply-StagedUpdate {
    $pending = Join-Path $Data "pending-update"
    $ready = Join-Path $pending "READY"
    $tree = Join-Path $pending "tree"
    if (-not (Test-Path $ready) -or -not (Test-Path (Join-Path $tree "backend"))) { return }
    $sv = ""
    try { $sv = (Get-Content $ready -Raw).Trim().TrimStart("v") } catch { $sv = "" }
    $iv = Get-AppVersion (Join-Path $Data "app\backend\config.py")
    if ($iv -and -not (Test-VersionGt $sv $iv)) {
        Write-Log "staged update $sv is not newer than $iv"
        Remove-Item $pending -Recurse -Force -ErrorAction SilentlyContinue
        return
    }
    Write-Log "apply staged update from local disk"
    $dest = Join-Path $Data "app"
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & robocopy $tree $dest /MIR /XD temp .venv /XF cookies.txt /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
    $code = $LASTEXITCODE
    $ErrorActionPreference = $prev
    if ($code -ge 8) {
        Write-Log "staged update skipped code=$code"
        return
    }
    Remove-Item $pending -Recurse -Force -ErrorAction SilentlyContinue
    Write-Log "staged update applied"
}

function Test-DepsReady {
    $py = Join-Path $Data "venv\Scripts\python.exe"
    if (-not (Test-Path $py)) { return $false }
    & $py -c "import fastapi, uvicorn, yt_dlp, webview" *> $null
    if ($LASTEXITCODE -ne 0) { return $false }
    $ff = Join-Path $Data "bin\ffmpeg.exe"
    $fp = Join-Path $Data "bin\ffprobe.exe"
    if ((Test-Path $ff) -and (Test-Path $fp)) { return $true }
    $haveFf = Get-Command ffmpeg -ErrorAction SilentlyContinue
    $haveFp = Get-Command ffprobe -ErrorAction SilentlyContinue
    if ($haveFf -and $haveFp) { return $true }
    return $false
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
    & $py -c "import fastapi, uvicorn, yt_dlp, webview" *> $null
    if ($LASTEXITCODE -eq 0) {
        Write-Log "venv ready; skip package install"
        return
    }
    Write-Log "install python packages"
    $req = Join-Path $Data "app\requirements.txt"
    $indexes = @(
        "https://mirrors.aliyun.com/pypi/simple/",
        "https://mirrors.cloud.tencent.com/pypi/simple",
        "https://pypi.mirrors.ustc.edu.cn/simple/",
        "https://pypi.tuna.tsinghua.edu.cn/simple",
        "https://pypi.org/simple"
    )
    $ok = $false
    foreach ($idx in $indexes) {
        $indexHost = ([Uri]$idx).Host
        Write-Log "try index $idx"
        if (Test-Path $uv) {
            & $uv pip install --python $py -r $req --index-url $idx
            if ($LASTEXITCODE -eq 0) { $ok = $true; break }
            Write-Log "uv pip failed on $idx"
        }
        & $py -m pip install -r $req -i $idx --trusted-host $indexHost --timeout 60 --retries 2
        if ($LASTEXITCODE -eq 0) { $ok = $true; break }
        Write-Log "pip failed on $idx"
    }
    if (-not $ok) { Fail "没法安装依赖。请检查网络后再打开一次。" }
}

function Start-Server {
    if (Test-HelperReady) { return }
    Stop-Bootstrap
    Start-Sleep -Milliseconds 200
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
    Write-Log "server still starting after 90s; leaving the window open"
}

try {
    Use-LocalApp
    Apply-StagedUpdate
    if (Test-DepsReady) {
        # FAST_PATH_START: installed copy, no GitHub, then the usable window.
        Write-Log "start from installed copy"
        Start-Server
        Open-DesktopWindow
        Wait-Ready
        # FAST_PATH_END
    } else {
        # SLOW_PATH_START: show a window before any first-run download.
        Write-Log "first-run setup; show window before downloads"
        Open-DesktopWindow
        Write-Host "正在准备运行环境…"
        Ensure-Uv
        Write-Host "正在安装依赖…"
        Ensure-Venv
        Write-Host "正在准备音频组件…"
        Ensure-Ffmpeg
        Start-Server
        Wait-Ready
        # SLOW_PATH_END
    }
    Write-Log "launch ok"
    Stop-Splash
    Open-DesktopWindow
    Wait-DesktopWindow
} catch {
    $detail = $_.Exception.Message
    try { Write-Log "catch $detail" } catch {}
    try { Stop-Bootstrap } catch {}
    Fail "没法安装运行环境。请检查网络后再打开一次。"
}
exit 0
