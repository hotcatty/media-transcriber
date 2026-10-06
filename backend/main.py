"""
媒体转录器 — localhost Web API.

粘贴小宇宙 / B 站 / 小红书 / 苹果播客分享链接（本地版另含 YouTube），或上传音视频，输出简体、有标点、
有段落的逐字稿，方便喂给自己的 AI。
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
import subprocess
import sys
import time
from collections import defaultdict, deque
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
from text_utils import (
    PUBLIC_BUSY_HINT,
    PUBLIC_COOKIE_HINT,
    PUBLIC_HOST_HINT,
    PUBLIC_RATE_HINT,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

config.ensure_dirs()
store = TaskStore()
jobs = JobManager(store)


def _public(task):
    pos, total = jobs.queue_line(task.id)
    return public_task(
        task,
        queue_ahead=max(0, pos - 1) if pos else 0,
        queue_position=pos,
        queue_total=total,
    )


SESSION_COOKIE = "mt_sid"
_PUBLIC_SUBMIT_LIMIT = 8
_PUBLIC_SUBMIT_WINDOW = 3600
_PUBLIC_UPLOAD_MAX_MB = 80
_public_submits: dict[str, deque] = defaultdict(deque)


def _public_rate_ok(session_id: str) -> bool:
    if not session_id:
        return True
    now = time.time()
    q = _public_submits[session_id]
    while q and now - q[0] > _PUBLIC_SUBMIT_WINDOW:
        q.popleft()
    if len(q) >= _PUBLIC_SUBMIT_LIMIT:
        return False
    q.append(now)
    return True


def _session_has_active(session_id: str) -> bool:
    if not session_id:
        return False
    return any(t.session_id == session_id for t in store.active())


def _is_local_helper(request: Request) -> bool:
    host = (request.headers.get("host") or "").split(":")[0].lower().strip("[]")
    return host in {"127.0.0.1", "localhost", "::1"}


def _isolate_visitors(request: Request) -> bool:
    """Public trial is multi-tenant; localhost desktop is one machine."""
    return bool(config.PUBLIC_WEB) or not _is_local_helper(request)


def _valid_session(raw: str) -> bool:
    s = (raw or "").strip()
    return 20 <= len(s) <= 64 and all(c.isalnum() or c in "-_" for c in s)


def _pick_session(request: Request) -> tuple[str, bool]:
    """Cookie is easy to drop in WeChat/Safari on HTTP; header/localStorage restore it."""
    cookie = request.cookies.get(SESSION_COOKIE, "")
    header = (request.headers.get("x-mt-sid") or "").strip()
    query = ""
    path = request.url.path or ""
    if path.endswith("/stream") or "/task-stream/" in path:
        query = (request.query_params.get("sid") or "").strip()
    for cand in (header, query, cookie):
        if _valid_session(cand):
            return cand, cand != cookie
    return secrets.token_urlsafe(24), True


def _write_session(request: Request) -> str:
    if _isolate_visitors(request):
        return getattr(request.state, "session_id", "") or ""
    return ""


def _owned_task(request: Request, task_id: str):
    task = store.get(task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    if _isolate_visitors(request):
        sid = getattr(request.state, "session_id", "") or ""
        if not sid or task.session_id != sid:
            raise HTTPException(404, "任务不存在")
    return task


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
    docs_url=None if config.PUBLIC_WEB else "/docs",
    redoc_url=None if config.PUBLIC_WEB else "/redoc",
    openapi_url=None if config.PUBLIC_WEB else "/openapi.json",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-MT-SID"],
)


@app.middleware("http")
async def visitor_session(request: Request, call_next):
    if not _isolate_visitors(request):
        request.state.session_id = ""
        return await call_next(request)
    sid, refresh_cookie = _pick_session(request)
    request.state.session_id = sid
    response = await call_next(request)
    if refresh_cookie:
        response.set_cookie(
            SESSION_COOKIE,
            sid,
            max_age=60 * 60 * 24 * 180,
            httponly=True,
            samesite="lax",
            path="/",
        )
    response.headers["X-MT-SID"] = sid
    response.headers["Access-Control-Expose-Headers"] = "X-MT-SID"
    return response


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
    return FileResponse(
        str(STATIC_DIR / "index.html"),
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/ping")
async def ping():
    return {
        "ok": True,
        "app": "media-transcriber",
        "preparing": False,
        "public_web": bool(config.PUBLIC_WEB),
        "youtube": not config.PUBLIC_WEB,
    }


@app.get("/api/health")
async def health(request: Request):
    info = engines.describe()
    ffmpeg_hint = audio_utils.check_ffmpeg()
    interrupted = []
    if not _isolate_visitors(request):
        interrupted = [
            _public(t) for t in store.list(limit=20)
            if t.status == "interrupted"
        ]
    return {
        "ok": info.get("ok") and not ffmpeg_hint,
        "ffmpeg": None if not ffmpeg_hint else ffmpeg_hint,
        "port": config.PORT,
        "public_web": bool(config.PUBLIC_WEB),
        "youtube": not config.PUBLIC_WEB,
        "engine": info if not _isolate_visitors(request) else {"ok": info.get("ok")},
        "interrupted": interrupted,
    }


@app.get("/api/settings")
async def get_settings(request: Request):
    if _isolate_visitors(request):
        return {"engine": "local"}
    return settings_store.get_all(redact_secrets=True)


@app.post("/api/settings")
async def post_settings(request: Request):
    if _isolate_visitors(request):
        raise HTTPException(404, "任务不存在")
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        raise HTTPException(400, "无效的设置")
    return settings_store.update(body)


@app.get("/api/cookie-status")
async def cookie_status(request: Request):
    if not _is_local_helper(request):
        return {"exists": False, "size": 0}
    exists = config.COOKIE_FILE.exists()
    size = config.COOKIE_FILE.stat().st_size if exists else 0
    return {"exists": exists, "size": size}


@app.post("/api/set-cookie")
async def set_cookie(request: Request, content: str = Form(...)):
    if not _is_local_helper(request):
        raise HTTPException(400, "网页版读不到你电脑里的登录状态")
    content = content.strip()
    if not content:
        raise HTTPException(400, "Cookie 内容为空")
    config.COOKIE_FILE.write_text(content + "\n", encoding="utf-8")
    jobs.reload_cookies()
    logger.info(f"Cookie 已更新: {config.COOKIE_FILE}")
    return {"ok": True, "message": "Cookie 已保存，下次解析链接时生效"}


@app.delete("/api/set-cookie")
async def delete_cookie(request: Request):
    if not _is_local_helper(request):
        raise HTTPException(400, "网页版读不到你电脑里的登录状态")
    if config.COOKIE_FILE.exists():
        config.COOKIE_FILE.unlink()
        jobs.reload_cookies()
    return {"ok": True, "message": "Cookie 已清除"}


@app.get("/api/browsers")
async def list_browsers(request: Request):
    if not _is_local_helper(request):
        return {"browsers": []}
    return {"browsers": cookie_import.list_installed_browsers()}


@app.post("/api/upload-cookies")
async def upload_cookies(
    request: Request,
    file: Optional[UploadFile] = File(None),
    content: str = Form(default=""),
    site: str = Form(default="bilibili"),
):
    if _isolate_visitors(request):
        raise HTTPException(400, PUBLIC_COOKIE_HINT)
    raw = (content or "").strip()
    if file is not None and (file.filename or "").strip():
        data = await file.read()
        raw = data.decode("utf-8", "replace")
    try:
        result = cookie_import.save_netscape_text(raw, site)
    except cookie_import.CookieImportError as e:
        raise HTTPException(400, str(e)) from None
    except Exception:
        logger.exception("上传登录状态失败")
        raise HTTPException(400, "上传失败，请稍后再试") from None
    jobs.reload_cookies()
    logger.info("已读取上传的 %s 登录状态", result.get("site"))
    return {"ok": True, "message": result["message"], "site": result.get("site")}


@app.post("/api/import-cookies")
async def import_cookies(
    request: Request,
    browser: str = Form(...),
    site: str = Form(default="bilibili"),
):
    if not _is_local_helper(request):
        raise HTTPException(400, "网页版请上传 cookies.txt")
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
    request: Request,
    url: str = Form(default=""),
    file: Optional[UploadFile] = File(None),
):
    hint = audio_utils.check_ffmpeg()
    if hint:
        raise HTTPException(500, hint)

    sid = _write_session(request)
    if _isolate_visitors(request):
        if not _public_rate_ok(sid):
            raise HTTPException(429, PUBLIC_RATE_HINT)
        if _session_has_active(sid):
            raise HTTPException(429, PUBLIC_BUSY_HINT)

    if file is not None and (file.filename or "").strip():
        return await _enqueue_upload(file, sid, public=_isolate_visitors(request))

    stripped = (url or "").strip()
    if not stripped:
        raise HTTPException(400, "请粘贴分享链接，或上传音视频文件")
    if len(stripped) > 2000:
        raise HTTPException(400, PUBLIC_HOST_HINT)
    task = await jobs.submit_url(stripped, session_id=sid)
    return {"task_id": task.id, "message": task.message, "status": task.status}


# Back-compat aliases for the previous UI.
@app.post("/api/process-video")
async def process_video(
    request: Request,
    url: str = Form(default=""),
    file: Optional[UploadFile] = File(None),
    summary_language: str = Form(default="zh"),
    api_key: str = Form(default=""),
    model_base_url: str = Form(default=""),
    model_id: str = Form(default=""),
):
    return await transcribe(request=request, url=url, file=file)


@app.post("/api/process-podcast")
async def process_podcast(request: Request, url: str = Form(...)):
    return await transcribe(request=request, url=url, file=None)


@app.post("/api/process-upload")
async def process_upload(request: Request, file: UploadFile = File(...)):
    return await transcribe(request=request, url="", file=file)


async def _enqueue_upload(file: UploadFile, session_id: str = "", public: bool = False) -> dict:
    raw_name = file.filename or "upload.bin"
    if ".." in raw_name or "/" in raw_name or "\\" in raw_name:
        raise HTTPException(400, "无效的文件名")
    safe_name = Path(raw_name).name
    ext = Path(safe_name).suffix.lower()
    if ext not in config.UPLOAD_ALLOWED_EXT:
        raise HTTPException(400, f"不支持的文件类型：{ext or '(无)'}")

    max_mb = _PUBLIC_UPLOAD_MAX_MB if public else config.UPLOAD_MAX_MB
    max_bytes = max_mb * 1024 * 1024
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
                raise HTTPException(413, f"文件超过 {max_mb} MB 限制")
            out_f.write(chunk)
    if total == 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "文件为空")

    task = await jobs.submit_upload(dest, safe_name, session_id=session_id)
    return {"task_id": task.id, "message": task.message}


@app.get("/api/tasks")
async def list_tasks(request: Request, limit: int = 50):
    if _isolate_visitors(request):
        items = store.list(limit=limit, session_id=_write_session(request))
    else:
        items = store.list(limit=limit)
    return {"tasks": [_public(t) for t in items]}


@app.get("/api/task-status/{task_id}")
@app.get("/api/tasks/{task_id}")
async def get_task(request: Request, task_id: str):
    return _public(_owned_task(request, task_id))


@app.get("/api/task-stream/{task_id}")
@app.get("/api/tasks/{task_id}/stream")
async def task_stream(request: Request, task_id: str):
    _owned_task(request, task_id)

    async def event_generator():
        queue = jobs.subscribe(task_id)
        try:
            current = store.get(task_id)
            if current:
                yield f"data: {json.dumps(_public(current), ensure_ascii=False)}\n\n"
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
async def resume_task(request: Request, task_id: str):
    _owned_task(request, task_id)
    try:
        task = await jobs.resume(task_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"task_id": task.id, "message": task.message}


@app.post("/api/tasks/{task_id}/cancel")
async def cancel_task(request: Request, task_id: str):
    _owned_task(request, task_id)
    await jobs.cancel(task_id)
    return {"ok": True}


@app.delete("/api/task/{task_id}")
@app.delete("/api/tasks/{task_id}")
async def delete_task(request: Request, task_id: str, remove_files: bool = True):
    _owned_task(request, task_id)
    await jobs.cancel(task_id)
    if not store.delete(task_id, remove_files=remove_files):
        raise HTTPException(404, "任务不存在")
    return {"ok": True}


def _file_response(path: Path, fmt: str) -> FileResponse:
    media = formats.FORMATS.get(fmt, {}).get("media_type", "text/plain; charset=utf-8")
    return FileResponse(path, filename=path.name, media_type=media)


@app.get("/api/tasks/{task_id}/export/{fmt}")
async def export_task(request: Request, task_id: str, fmt: str):
    _owned_task(request, task_id)
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
async def legacy_download(request: Request, filename: str):
    if _isolate_visitors(request):
        raise HTTPException(404, "文件不存在")
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
async def task_text(request: Request, task_id: str):
    """Plain reflowed body — what you paste into a model."""
    _owned_task(request, task_id)
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
async def download_audio(request: Request, task_id: str, download: bool = False):
    _owned_task(request, task_id)
    path = _task_audio_path(task_id)
    suffix = path.suffix.lower() or ".m4a"
    media = "audio/mp4" if suffix in (".m4a", ".mp4") else "application/octet-stream"
    if download:
        return FileResponse(path, filename=path.name, media_type=media)
    return FileResponse(
        path,
        media_type=media,
        headers={"Content-Disposition": "inline"},
    )


@app.post("/api/tasks/{task_id}/reveal-audio")
async def reveal_audio(request: Request, task_id: str):
    if _isolate_visitors(request):
        raise HTTPException(404, "任务不存在")
    _owned_task(request, task_id)
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
