# 猫听转文字

<img src="packaging/icons/app-icon.png" width="128" height="128" alt="猫听转文字">

把音视频链接转成简体中文逐字稿。

## 三个版本

| | 线上体验版 | Skill 版（给 AI 用） | 本地桌面版 |
|---|---|---|---|
| 入口 | http://maoting.online | 把下面那段话发给 Cursor / Claude Code / Codex | 本页 [Release](https://github.com/hotcatty/media-transcriber/releases/latest) 安装包 |
| 怎么用 | 打开网页，粘贴链接 | 「把这个视频转成文字」 | 打开应用，粘贴链接 |
| 平台 | 小宇宙、B 站、小红书、抖音、苹果播客 | 增加youtube | 同左 |
| 速度 | 约为视频时间的1/3 | 约为视频时间的1/10 | 同左 |

线上版主要是体验。已经在用 AI 编程助手的话，用 Skill 版最省事。使用频率高的话，可以选用本地桌面版。

## 线上体验版

打开 http://maoting.online ，粘贴链接即可进行转录。

由于是大陆机房，所以无法转录YouTube等海外音视频。

## Skill 版

点下面代码块右上角复制，粘贴任意ai agent(如workbuddy、千问办公、codex、cursor等）：

```text
帮我安装「猫听转文字」本地转录 Skill。
它在这个 GitHub 仓库：https://github.com/hotcatty/media-transcriber
请先打开或 git clone 上面这个仓库，不要在当前其它项目里找 README。
Claude Code 请 clone 到 ~/.claude/skills/media-transcriber
Codex 请 clone 到 ~/.codex/skills/media-transcriber
Cursor 已经打开该仓库就先 git pull 再用；否则先 clone 再打开。
然后：
1. 读这个仓库的 SKILL.md（https://github.com/hotcatty/media-transcriber/blob/main/SKILL.md）
2. 在该仓库根目录跑 bash scripts/run.sh ，缺 Python / ffmpeg / 依赖就按脚本提示装
3. 模型走 hf-mirror.com（国内默认）
4. 装完告诉我可以开始贴链接了
```

它会逐条问你要不要执行命令，点同意就行。第一次需要安装下语音模型，之后便可正常对话。
已经 clone 了本仓库时，Cursor 会读 `.cursor/skills/media-transcriber/`。也可以把仓库放到 `~/.claude/skills/media-transcriber/` 或 `~/.codex/skills/media-transcriber/` 给 Claude Code / Codex 用。

## 本地桌面版

请于 [Releases](https://github.com/hotcatty/media-transcriber/releases/latest) 下载对应系统的压缩包。

## 安全

- **本地桌面版 / Skill 版**：音频、逐字稿、历史记录都在本机，不会发到服务器。
- **线上体验版**：链接会在体验服务器上下载并识别，历史记录只留在浏览器里。
- 充电视频：请用**已经充过电的账号**的登录状态再试，否则下不到音频。

## 技术原理

- 语音识别用 [OpenAI Whisper](https://github.com/openai/whisper)（开源）。本地版用更大、更快的 Turbo 模型（苹果芯片走 MLX，其它电脑走 faster-whisper）；线上体验版用更小的模型。
- 链接解析和音频下载用 [yt-dlp](https://github.com/yt-dlp/yt-dlp)，再用 ffmpeg 抽成适合识别的音频。
- 页面标题、简介等会当作识别时的提示，以减少人名、术语转录上的错误。

## 意见反馈

使用中有问题或建议，可以加微信 **edecev2009**，添加时请备注清楚来意（例如「猫听转文字反馈」）。

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
