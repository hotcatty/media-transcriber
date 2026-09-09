"""
媒体转录器 — localhost Web API.

粘贴小宇宙 / B 站 / YouTube 分享链接，或上传音视频，输出简体、有标点、
有段落的逐字稿，方便喂给自己的 AI。
"""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

import audio as audio_utils
import config
import cookie_import
import engines
import formats
import settings_store
from jobs import JobManager, public_task
from task_store import TaskStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

config.ensure_dirs()
store = TaskStore()
jobs = JobManager(store)


@asynccontextmanager
async def lifespan(_: FastAPI):
    hint = audio_utils.check_ffmpeg()
    if hint:
        logger.warning(hint)
    store.prune()
    jobs.start()
    logger.info(f"engine: {engines.describe()}")
    yield
    jobs.stop()


app = FastAPI(
    title="媒体转录器",
    version="3.0.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def allow_private_network(request: Request, call_next):
    """Allow the public door page (HTTPS) to probe this local helper."""
    if (
        request.method == "OPTIONS"
        and request.headers.get("access-control-request-private-network") == "true"
    ):
        origin = request.headers.get("origin", "*")
        return Response(
            status_code=204,
            headers={
                "Access-Control-Allow-Origin": origin,
                "Access-Control-Allow-Methods": "GET,POST,PUT,PATCH,DELETE,OPTIONS",
                "Access-Control-Allow-Headers": request.headers.get(
                    "access-control-request-headers", "*"
                ),
                "Access-Control-Allow-Private-Network": "true",
            },
        )
    response = await call_next(request)
    response.headers["Access-Control-Allow-Private-Network"] = "true"
    return response

STATIC_DIR = config.PROJECT_ROOT / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def read_root():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/ping")
async def ping():
    return {"ok": True, "app": "media-transcriber", "preparing": False}


@app.get("/api/health")
async def health():
    info = engines.describe()
    ffmpeg_hint = audio_utils.check_ffmpeg()
    return {
        "ok": info.get("ok") and not ffmpeg_hint,
        "ffmpeg": None if not ffmpeg_hint else ffmpeg_hint,
        "port": config.PORT,
        "engine": info,
        "interrupted": [
            public_task(t) for t in store.list(limit=20)
            if t.status == "interrupted"
        ],
    }


@app.get("/api/settings")
async def get_settings():
    return settings_store.get_all(redact_secrets=True)


@app.post("/api/settings")
async def post_settings(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        raise HTTPException(400, "无效的设置")
    return settings_store.update(body)


@app.get("/api/cookie-status")
async def cookie_status():
    exists = config.COOKIE_FILE.exists()
    size = config.COOKIE_FILE.stat().st_size if exists else 0
    return {"exists": exists, "size": size}


@app.post("/api/set-cookie")
async def set_cookie(content: str = Form(...)):
    content = content.strip()
    if not content:
        raise HTTPException(400, "Cookie 内容为空")
    config.COOKIE_FILE.write_text(content + "\n", encoding="utf-8")
    jobs.reload_cookies()
    logger.info(f"Cookie 已更新: {config.COOKIE_FILE}")
    return {"ok": True, "message": "Cookie 已保存，下次解析链接时生效"}


@app.delete("/api/set-cookie")
async def delete_cookie():
    if config.COOKIE_FILE.exists():
        config.COOKIE_FILE.unlink()
        jobs.reload_cookies()
    return {"ok": True, "message": "Cookie 已清除"}


@app.get("/api/browsers")
async def list_browsers():
    return {"browsers": cookie_import.list_installed_browsers()}


@app.post("/api/import-cookies")
async def import_cookies(browser: str = Form(...), site: str = Form(default="bilibili")):
    try:
        result = await asyncio.to_thread(
            cookie_import.import_from_browser, browser, site
        )
    except cookie_import.CookieImportError as e:
        raise HTTPException(400, str(e)) from None
    except Exception:
        logger.exception("从浏览器读取登录状态失败")
        raise HTTPException(400, "读取失败，请稍后再试") from None
    jobs.reload_cookies()
    logger.info("从 %s 读取 %s 登录状态成功", result.get("browser"), result.get("site"))
    return {"ok": True, "message": result["message"], "site": result.get("site")}


@app.post("/api/transcribe")
async def transcribe(
    url: str = Form(default=""),
    file: Optional[UploadFile] = File(None),
):
    hint = audio_utils.check_ffmpeg()
    if hint:
        raise HTTPException(500, hint)

    if file is not None and (file.filename or "").strip():
        return await _enqueue_upload(file)

    stripped = (url or "").strip()
    if not stripped:
        raise HTTPException(400, "请粘贴分享链接，或上传音视频文件")
    task = await jobs.submit_url(stripped)
    return {"task_id": task.id, "message": task.message, "status": task.status}


# Back-compat aliases for the previous UI.
@app.post("/api/process-video")
async def process_video(
    url: str = Form(default=""),
    file: Optional[UploadFile] = File(None),
    summary_language: str = Form(default="zh"),
    api_key: str = Form(default=""),
    model_base_url: str = Form(default=""),
    model_id: str = Form(default=""),
):
    return await transcribe(url=url, file=file)


@app.post("/api/process-podcast")
async def process_podcast(url: str = Form(...)):
    return await transcribe(url=url, file=None)


@app.post("/api/process-upload")
async def process_upload(file: UploadFile = File(...)):
    return await transcribe(url="", file=file)


async def _enqueue_upload(file: UploadFile) -> dict:
    raw_name = file.filename or "upload.bin"
    if ".." in raw_name or "/" in raw_name or "\\" in raw_name:
        raise HTTPException(400, "无效的文件名")
    safe_name = Path(raw_name).name
    ext = Path(safe_name).suffix.lower()
    if ext not in config.UPLOAD_ALLOWED_EXT:
        raise HTTPException(400, f"不支持的文件类型：{ext or '(无)'}")

    max_bytes = config.UPLOAD_MAX_MB * 1024 * 1024
    import uuid as _uuid
    dest = config.UPLOAD_DIR / f"{_uuid.uuid4().hex[:12]}{ext}"

    total = 0
    with open(dest, "wb") as out_f:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                dest.unlink(missing_ok=True)
                raise HTTPException(413, f"文件超过 {config.UPLOAD_MAX_MB} MB 限制")
            out_f.write(chunk)
    if total == 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "文件为空")

    task = await jobs.submit_upload(dest, safe_name)
    return {"task_id": task.id, "message": task.message}


@app.get("/api/tasks")
async def list_tasks(limit: int = 50):
    return {"tasks": [public_task(t) for t in store.list(limit=limit)]}


@app.get("/api/task-status/{task_id}")
@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str):
    task = store.get(task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    return public_task(task)


@app.get("/api/task-stream/{task_id}")
@app.get("/api/tasks/{task_id}/stream")
async def task_stream(task_id: str):
    task = store.get(task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")

    async def event_generator():
        queue = jobs.subscribe(task_id)
        try:
            current = store.get(task_id)
            if current:
                yield f"data: {json.dumps(public_task(current), ensure_ascii=False)}\n\n"
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=25.0)
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    if payload.get("status") in ("completed", "failed", "cancelled", "needs_login"):
                        break
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'type': 'heartbeat'}, ensure_ascii=False)}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            jobs.unsubscribe(task_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
        },
    )


@app.post("/api/tasks/{task_id}/resume")
async def resume_task(task_id: str):
    try:
        task = await jobs.resume(task_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"task_id": task.id, "message": task.message}


@app.post("/api/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    if store.get(task_id) is None:
        raise HTTPException(404, "任务不存在")
    await jobs.cancel(task_id)
    return {"ok": True}


@app.delete("/api/task/{task_id}")
@app.delete("/api/tasks/{task_id}")
async def delete_task(task_id: str, remove_files: bool = True):
    await jobs.cancel(task_id)
    if not store.delete(task_id, remove_files=remove_files):
        raise HTTPException(404, "任务不存在")
    return {"ok": True}


def _file_response(path: Path, fmt: str) -> FileResponse:
    media = formats.FORMATS.get(fmt, {}).get("media_type", "text/plain; charset=utf-8")
    return FileResponse(path, filename=path.name, media_type=media)


@app.get("/api/tasks/{task_id}/export/{fmt}")
async def export_task(task_id: str, fmt: str):
    if fmt not in formats.FORMATS:
        raise HTTPException(400, f"不支持的格式：{fmt}")
    path = store.read_export(task_id, fmt)
    if path is not None:
        return _file_response(path, fmt)
    task = store.get(task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    segs = store.load_segments(task_id)
    if not segs:
        raise HTTPException(404, "还没有可导出的正文")
    meta = formats.TranscriptMeta(
        title=task.title or "未命名",
        source=task.source,
        language=task.language,
        duration=task.duration,
        engine=task.engine,
    )
    body = formats.render(fmt, segs, meta)
    media = formats.FORMATS[fmt]["media_type"]
    return StreamingResponse(
        iter([body.encode("utf-8")]),
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="transcript.{fmt}"'},
    )


@app.get("/api/download/{filename}")
async def legacy_download(filename: str):
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(400, "文件名无效")
    for task in store.list(limit=500):
        for key, stored in task.files.items():
            if stored == filename:
                path = store.read_export(task.id, key)
                if path:
                    return _file_response(path, key)
    legacy = config.TEMP_DIR / filename
    if legacy.exists() and filename.endswith((".md", ".txt")):
        return FileResponse(legacy, filename=filename, media_type="text/plain")
    raise HTTPException(404, "文件不存在")


@app.get("/api/tasks/{task_id}/text")
async def task_text(task_id: str):
    """Plain reflowed body — what you paste into a model."""
    path = store.read_export(task_id, "txt")
    if path is None:
        raise HTTPException(404, "还没有逐字稿")
    return {"text": path.read_text(encoding="utf-8")}


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _audio_file_or_message(task_id: str):
    task = store.get(task_id)
    if task is None:
        return None, "任务不存在"
    if task.origin == "subtitle":
        return None, "这个任务用的是现成字幕，没有下载音频"
    if not task.audio_path:
        return None, "还没有音频文件"
    path = Path(task.audio_path).resolve()
    roots = (
        config.AUDIO_DIR.resolve(),
        config.UPLOAD_DIR.resolve(),
        config.TEMP_DIR.resolve(),
    )
    if not any(_is_under(path, root) for root in roots):
        return None, "音频路径无效"
    if not path.is_file():
        return None, "音频文件已丢失"
    return path, None


def _task_audio_path(task_id: str) -> Path:
    path, msg = _audio_file_or_message(task_id)
    if path is None:
        code = 400 if msg == "音频路径无效" else 404
        raise HTTPException(code, msg)
    return path


@app.get("/api/tasks/{task_id}/audio")
@app.get("/api/download-audio/{task_id}")
async def download_audio(task_id: str):
    path = _task_audio_path(task_id)
    suffix = path.suffix.lower() or ".m4a"
    media = "audio/mp4" if suffix in (".m4a", ".mp4") else "application/octet-stream"
    return FileResponse(path, filename=path.name, media_type=media)


@app.post("/api/tasks/{task_id}/reveal-audio")
async def reveal_audio(task_id: str):
    path, msg = _audio_file_or_message(task_id)
    if path is None:
        return {"ok": False, "message": msg}
    try:
        if sys.platform == "darwin":
            done = subprocess.run(["open", "-R", str(path)], capture_output=True, text=True)
            if done.returncode != 0:
                logger.warning("reveal audio failed: %s", (done.stderr or "").strip())
                return {"ok": False, "message": "音频文件已丢失"}
        elif sys.platform == "win32":
            subprocess.Popen(["explorer", f"/select,{path}"])
        else:
            subprocess.Popen(["xdg-open", str(path.parent)])
    except Exception:
        logger.exception("reveal audio failed")
        return {"ok": False, "message": "无法在文件夹中显示音频"}
    return {"ok": True}


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    if isinstance(exc, HTTPException):
        detail = exc.detail
        message = detail if isinstance(detail, str) else "出了点问题，请稍后再试"
        return JSONResponse(
            {"ok": False, "message": message, "detail": detail},
            status_code=exc.status_code,
        )
    logger.exception("unhandled error")
    payload = {"ok": False, "message": "出了点问题，请稍后再试", "detail": "出了点问题，请稍后再试"}
    if request.url.path.startswith("/api/"):
        return JSONResponse(payload, status_code=200)
    return JSONResponse(payload, status_code=500)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=config.HOST, port=config.PORT)
