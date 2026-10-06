# 转录小工具

把分享链接转成简体逐字稿，方便喂给自己的 AI。

识别用的是开源的 [Whisper](https://github.com/openai/whisper) 语音模型：先下载音频，再在本地或体验服务器上听写成文字。

## 三个版本

| | 线上体验版 | Skill 版（给 AI 用） | 本地桌面版 |
|---|---|---|---|
| 入口 | http://106.55.19.88 | 把下面那段话发给 Cursor / Claude Code / Codex | 本页 [Release](https://github.com/hotcatty/media-transcriber/releases/latest) 安装包 |
| 怎么用 | 打开网页，粘贴链接 | 直接说人话：「把这个视频转成文字」 | 下载安装后，打开同一个界面自己贴 |
| 平台 | 小宇宙、B 站、小红书、苹果播客 | 小宇宙、B 站、小红书、YouTube、苹果播客 | 同左 |
| 速度 | 人多会排队 | 本机 Turbo，大约比线上快三四倍 | 同左 |
| 数据 | 在体验服务器上处理 | 音频和文稿只留在你电脑上 | 同左 |

线上版主要是体验。已经在用 AI 编程助手的话，用 Skill 版最省事：不用装桌面应用，让 AI 自己装依赖、转录、再帮你分析。想要一个独立窗口，再用本地桌面版。

## 线上体验版

打开 http://106.55.19.88 ，把链接贴进去，点转录。

人多的时候页面会告诉你排在第几位。大陆机房连不上 YouTube，所以线上版没有海外接口；苹果播客可以。

## Skill 版（推荐给正在用 Cursor / Claude Code 的人）

不用开终端，把这段话发给你的 AI：

```text
帮我把这个仓库的转录 skill 装好：
1. 读 README 里「Skill 版」和 SKILL.md
2. 跑 bash scripts/run.sh ，缺 Python / ffmpeg / 依赖就按它的提示装
3. 模型走 hf-mirror.com（国内默认）
4. 装完告诉我可以开始贴链接了
```

它会逐条问你要不要执行命令，点同意就行。第一次主要在下语音模型，之后就不用管。

装完直接说人话：

```text
把这个视频转成文字 https://...
这条链接讲了什么
拆解一下这期播客，开头钩子是怎么做的
```

文字稿会出现在对话里，同时落到 `~/transcripts/` 下面。AI 会先给你结论和要点，需要原文再看文件。

已经 clone 了本仓库时，Cursor 会读 `.cursor/skills/media-transcriber/`。也可以把仓库放到 `~/.claude/skills/media-transcriber/` 或 `~/.codex/skills/media-transcriber/` 给 Claude Code / Codex 用。

## 本地桌面版

在 [Releases](https://github.com/hotcatty/media-transcriber/releases/latest) 下载对应系统的压缩包。

**Mac**  
https://github.com/hotcatty/media-transcriber/releases/latest/download/MediaTranscriber-macOS.zip

1. 解压，打开「转录小工具」
2. 系统弹出「未打开」时点「完成」，不要点「移到废纸篓」
3. 打开系统设置 → 隐私与安全性 → 「仍要打开」

**Windows**  
https://github.com/hotcatty/media-transcriber/releases/latest/download/MediaTranscriber-windows.zip

1. 解压，双击 `start.bat`
2. 如果弹出「Windows 已保护你的电脑」，点「更多信息」，再点「仍要运行」

第一次打开会自动准备运行环境和语音模型（步骤里是「下载turbo模型」，大约 1.6 GB），需要等几分钟。以后再打开就可以直接贴链接。本地版支持小宇宙、B 站、小红书、YouTube、苹果播客。

## 安全

- **本地桌面版 / Skill 版**：音频、逐字稿、历史记录都在你这台电脑上，不会发到我们的服务器。需要登录才能看的内容，桌面版可点「读取登录状态」；Skill 版同样走本机，读不到账号密码，也不会上传。
- **线上体验版**：你粘贴的链接会在体验服务器上下载并识别。适合公开内容。历史记录只留在你这台浏览器里，别人打开网站看不到。不接受登录 Cookie，也不开放设置接口。账号密码我们要不到。
- 充电视频：请用**已经充过电的账号**的登录状态再试，否则只能看到需要登录，下不到音频。
- 本项目开源，代码都在这个仓库里，可以自己查看。

## 技术原理

- 语音识别用 [OpenAI Whisper](https://github.com/openai/whisper)（开源）。本地版用更大、更快的 Turbo 模型（苹果芯片走 MLX，其它电脑走 faster-whisper）；线上体验版用更小的模型、一次只处理一条，所以人多会慢。
- 链接解析和音频下载用 [yt-dlp](https://github.com/yt-dlp/yt-dlp)，再用 ffmpeg 抽成适合识别的音频。
- 页面标题、简介等会当作识别时的提示，减少人名、术语听错。

## 意见反馈

使用中有问题或建议，可以加微信 **edecev2009**，添加时请备注清楚来意（例如「转录小工具反馈」），通过后再说具体问题。

## 自己改代码

从源码运行需要 Python 3.10+ 和 [ffmpeg](https://ffmpeg.org/)。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python start.py --desktop
```

只起本机服务、用浏览器看：`python start.py --prod`，打开 http://127.0.0.1:8766

国内下载模型默认走 HuggingFace 镜像。可选环境变量见 `.env.example`。打包脚本在 `packaging/`。
