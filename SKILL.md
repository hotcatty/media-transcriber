---
name: media-transcriber
description: 把视频/音频链接或本地文件转成简体文字稿并做内容分析。适用于小宇宙、B 站、小红书、YouTube、苹果播客，以及「把这个视频转成文字」「这条链接讲了什么」「拆解这期播客」。全本地 Whisper，不上传。Use when the user pastes a media URL, asks to transcribe, summarize, or analyze a video/podcast/audio file.
---

# 转录小工具 Skill

`$SKILL` = 本仓库根目录（已打开这个项目时），或 clone 到 `~/.claude/skills/media-transcriber/` / `~/.codex/skills/media-transcriber/` 的那份。

全程本地跑：yt-dlp 拉音频，本机 Whisper Turbo 听写。不要把音视频或 Cookie 发到云端。

## 用法

```bash
bash $SKILL/scripts/run.sh "<链接或本地文件>"
```

指定输出目录：

```bash
bash $SKILL/scripts/run.sh "<链接或本地文件>" --out "<目录>"
```

第一次会装 Python 依赖并下载模型，之后同一台机器不用再装。缺 ffmpeg 时脚本会给出安装命令，装好再重跑。

## 产物

默认写到 `~/transcripts/<月日-时分秒>/`：

| 文件 | 用途 |
|---|---|
| `transcript.txt` | 分好段的可读稿，分析时读这个 |
| `transcript-timed.md` | 每句带时间戳 |
| `transcript.srt` | 字幕 |
| `info.md` | 标题 / 来源 / 时长 |
| `meta.json` | 程序用 |

## 你（AI）该怎么做

1. 用户丢链接或文件、说「转成文字 / 讲了什么 / 拆解一下」时，直接跑上面的命令，不要先问一堆参数。
2. 读 `transcript.txt`，**基于文稿给结论**：先一句话，再要点；不要把整篇原文糊回去，路径留给要原文的人。
3. 文稿为空或短到不像话（脚本会 `WARN`）：当作没口播/纯字卡，如实说，不要编。
4. 需要登录或充电视频：把脚本的中文提示原样告诉用户，让他们用本地网页版读取登录状态后再试，不要猜测内容。
5. 不要把下载的音频、Cookie、逐字稿提交进 git，除非用户明确要求。

## 平台

本地这条链路支持小宇宙、B 站、小红书、YouTube、苹果播客和本地音视频。线上体验站没有 YouTube。
