# 转录小工具

把分享链接转成简体逐字稿，方便喂给自己的 AI。识别用的是开源的 [Whisper](https://github.com/openai/whisper) 语音模型。

有两个版本：

| | 线上体验版 | 本地版 |
|---|---|---|
| 入口 | http://106.55.19.88 | 本页 Release 里的安装包 |
| 平台 | 小宇宙、B 站、小红书、苹果播客 | 上面这些，再加上 YouTube |
| 速度 | 方便，打开就能用；人多时会排队，会慢一些 | 大约比线上快三四倍（本机 Turbo） |
| 数据 | 跑在我们的体验服务器上 | 音频、Cookie、历史都在你自己电脑上 |

线上版主要是体验。本地版才是完整能力：支持 YouTube，也不跟别人抢同一台机器。

## 线上体验版

打开 http://106.55.19.88 粘贴链接即可。

大陆机房连不上 YouTube，所以线上版不做海外接口。苹果播客可以。人多的时候页面会提示排队位置。

## 本地版

在 [Releases](https://github.com/hotcatty/media-transcriber/releases/latest) 下载对应系统的压缩包：

- Mac：https://github.com/hotcatty/media-transcriber/releases/latest/download/MediaTranscriber-macOS.zip  
  解压后打开「转录小工具」。系统弹出「未打开」时点「完成」，不要点「移到废纸篓」；再到系统设置 → 隐私与安全性 → 「仍要打开」。
- Windows：https://github.com/hotcatty/media-transcriber/releases/latest/download/MediaTranscriber-windows.zip  
  解压后双击 `start.bat`。如果弹出「Windows 已保护你的电脑」，点「更多信息」再点「仍要运行」。

第一次打开会自动准备 Python、ffmpeg 和语音模型（步骤里是「下载turbo模型」）。之后再打开同一个应用就能贴链接转录。本地版支持小宇宙、B 站、小红书、YouTube、苹果播客。

开发时也可以：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python start.py --desktop
```

只起本机服务、用浏览器看界面：`python start.py --prod`，然后打开 http://127.0.0.1:8766

## 技术原理

- 语音识别用 [OpenAI Whisper](https://github.com/openai/whisper)（开源）。本地版默认 [Whisper large-v3 Turbo](https://github.com/openai/whisper)：Apple Silicon 走 [MLX](https://github.com/ml-explore/mlx-examples/tree/main/whisper)，其它机器走 [faster-whisper](https://github.com/SYSTRAN/faster-whisper)。
- 线上体验版同样是 Whisper，但用更小的 `small` 模型、单路排队，所以人多时会慢。本机 Turbo 通常比线上快三四倍。
- 链接解析和音频下载用 [yt-dlp](https://github.com/yt-dlp/yt-dlp)，再用 ffmpeg 抽成单声道再送进 Whisper。
- 页面标题、简介等会当作 Whisper 的热词/上下文，减少专有名词听错。

## 对本机组件会做什么

- 自动准备 Python 和 ffmpeg，不用自己装环境、不用开终端
- 第一次转录会再自动下载语音模型（约 1.6 GB）
- 音频、Cookie、历史都在本机，不会发到我们的服务器

## 自己改代码

需要：macOS 或 Linux，Python 3.10+，[ffmpeg](https://ffmpeg.org/)。Apple Silicon 会走 MLX GPU，其它机器走 faster-whisper。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python start.py --desktop
```

只起服务、用浏览器看：`python start.py --prod`，打开 http://127.0.0.1:8766

界面状态预览：https://hotcatty.github.io/media-transcriber/?lab=1

国内默认走 HuggingFace 镜像（`HF_ENDPOINT=https://hf-mirror.com`）。

打包：`packaging/build-macos-zip.sh`、`packaging/build-windows-zip.sh`。

## 登录可见 / 充电视频

需要登录才能看的内容：点「读取登录状态」，从本机浏览器导入 Cookie。文件写在本机，不会上传。充电视频请导入**已充电账号**的登录状态后再试。

## 数据在哪

| | 位置 |
|---|---|
| 历史和文稿（助手安装） | `~/Library/Application Support/media-transcriber/temp` |
| 登录 Cookie（助手安装） | `~/Library/Application Support/media-transcriber/cookies.txt` |
| 历史和文稿（源码运行） | 项目里的 `temp/` |
| 模型权重（Mac / Linux） | `~/.cache/media-transcriber/models` |
| 模型权重（Windows） | 有 D 盘等非 C 盘时用 `D:\media-transcriber\models`；只有 C 盘才落到用户目录缓存 |

这些路径都已加入 `.gitignore`。

## 环境变量

见 `.env.example`。本地转录默认不需要任何云端 Key。公网体验站会设 `MT_PUBLIC_WEB=1`，关掉 YouTube。
