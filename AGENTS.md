# Agent Instructions

本仓库是本地视频转文字工具。用户丢来链接或音视频文件、要文字稿或内容分析时，走 Skill 命令，不要自己另写一套 whisper 脚本。

`$SKILL` = 仓库根目录，或安装到 `~/.claude/skills/media-transcriber/`、`~/.codex/skills/media-transcriber/` 的副本。完整约定见 [SKILL.md](SKILL.md)。

```bash
bash $SKILL/scripts/run.sh "<链接或本地文件>"
```

然后读输出目录里的 `transcript.txt` 再回答：先结论，再要点，原文只给路径。音视频和 Cookie 留在本机，不要上传，也不要提交进 git。
