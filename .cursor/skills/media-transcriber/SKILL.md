---
name: media-transcriber
description: 把视频/音频链接或本地文件转成简体文字稿并做内容分析。适用于小宇宙、B 站、小红书、YouTube、苹果播客，以及「把这个视频转成文字」「这条链接讲了什么」「拆解这期播客」。全本地 Whisper，不上传。Use when the user pastes a media URL, asks to transcribe, summarize, or analyze a video/podcast/audio file.
---

# 转录小工具 Skill

当前工作区就是这个项目。完整约定在仓库根目录 `SKILL.md`。

```bash
bash scripts/run.sh "<链接或本地文件>"
```

跑完读输出目录里的 `transcript.txt`（默认 `~/transcripts/<时间>/`）：先一句话结论，再要点，不要把整篇原文糊给用户。文稿过短当作没口播。需要登录时把脚本提示原样告诉用户。不要把音频、Cookie、文稿提交进 git。
