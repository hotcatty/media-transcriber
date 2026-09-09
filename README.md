# 转录小工具

把小宇宙 / B 站 / YouTube 分享链接转成简体逐字稿。

**给朋友用，发这个网址就行：**

https://hotcatty.github.io/media-transcriber/

打开后粘贴链接，点「转录为逐字稿」。第一次会先解析链接，再自动下载语音模型（步骤里是「下载turbo模型」），然后继续下载音频和识别。

目前这一版先支持 Mac。

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
python start.py --prod
```

浏览器打开 http://127.0.0.1:8766

国内默认走 HuggingFace 镜像（`HF_ENDPOINT=https://hf-mirror.com`）。

Mac 助手的打包脚本：`packaging/build-macos-zip.sh`。

## 登录可见 / 充电视频

需要登录才能看的内容：点「读取登录状态」，从本机浏览器导入 Cookie。文件写在本机，不会上传。充电视频请导入**已充电账号**的登录状态后再试。

## 数据在哪

| | 位置 |
|---|---|
| 历史和文稿（助手安装） | `~/Library/Application Support/media-transcriber/temp` |
| 登录 Cookie（助手安装） | `~/Library/Application Support/media-transcriber/cookies.txt` |
| 历史和文稿（源码运行） | 项目里的 `temp/` |
| 模型权重 | `~/.cache/media-transcriber/models` |

这些路径都已加入 `.gitignore`。

## 环境变量

见 `.env.example`。本地转录默认不需要任何云端 Key。
