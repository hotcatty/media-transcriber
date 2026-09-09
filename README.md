# 转录小工具

把小宇宙 / B 站 / YouTube 分享链接转成简体逐字稿。网页在你自己电脑上打开，模型、音频、Cookie 和历史都留在本机。

这不是部署到 Vercel 就能用的云服务。GitHub 上是源码；真正转录在本机跑。

## 运行

需要：macOS 或 Linux，Python 3.10+，[ffmpeg](https://ffmpeg.org/)。Apple Silicon 会走 MLX GPU，其它机器走 faster-whisper。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python start.py --prod
```

浏览器打开 http://127.0.0.1:8766

第一次转录会自动下载语音模型（约 1.6 GB）。国内默认走 HuggingFace 镜像（`HF_ENDPOINT=https://hf-mirror.com`）。

## 登录可见 / 充电视频

需要登录才能看的内容：点「读取登录状态」，从本机浏览器导入 Cookie。文件写在项目根目录 `cookies.txt`，不会上传。充电视频请导入**已充电账号**的登录状态后再试。

## 数据在哪

| | 位置 |
|---|---|
| 历史和文稿 | `temp/` |
| 登录 Cookie | `cookies.txt` |
| 模型权重 | `~/.cache/media-transcriber/models` |

这些路径都已加入 `.gitignore`。

## 环境变量

见 `.env.example`。本地转录默认不需要任何云端 Key。
