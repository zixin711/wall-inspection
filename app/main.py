"""FastAPI 服务入口。

接口：
  POST /api/v1/analyze                 上传两张图，返回 task_id（202）
  GET  /api/v1/tasks/{id}/stream       SSE 分段进度：CV 结果先到，模型判读后到
  GET  /api/v1/tasks/{id}              最终结果
  GET  /api/v1/tasks/{id}/assets/{f}   图像资源
  DELETE /api/v1/tasks/{id}            用户删除本次记录
  GET  /api/v1/config                  前端启动所需的分级标签与文案
  GET  /api/v1/health                  健康检查（含大模型连通性）
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import pipeline
from .config import BASE_DIR, get_settings, grading_config
from .store import Store, new_task_id

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("mould")

settings = get_settings()
store = Store(settings.storage_path, settings.retention_days)

# task_id -> 进度队列。SSE 断开后由 finally 清理。
_channels: Dict[str, asyncio.Queue] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    n = store.purge_expired()
    if n:
        log.info("清理过期任务 %d 个", n)
    log.info("模型端点 %s / %s", settings.llm_base_url, settings.llm_model)
    yield


app = FastAPI(title="墙面霉菌 UV 分级检测", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=settings.cors_list,
    allow_credentials=False, allow_methods=["*"], allow_headers=["*"],
)


# ------------------------------------------------------------------ analyze

@app.post("/api/v1/analyze", status_code=202)
async def analyze(background: BackgroundTasks,
                  white: UploadFile = File(..., description="白光图"),
                  uv: Optional[UploadFile] = File(None, description="365nm UV 图"),
                  notes: str = Form(""),
                  locale: str = Form("")) -> Dict[str, Any]:
    limit = settings.max_upload_mb * 1024 * 1024
    white_bytes = await white.read()
    if len(white_bytes) > limit:
        raise HTTPException(413, f"白光图超过 {settings.max_upload_mb}MB 上限")
    uv_bytes: Optional[bytes] = None
    if uv is not None:
        uv_bytes = await uv.read()
        if len(uv_bytes) > limit:
            raise HTTPException(413, f"UV 图超过 {settings.max_upload_mb}MB 上限")
        if not uv_bytes:
            uv_bytes = None

    task_id = new_task_id()
    store.create(task_id)
    queue: asyncio.Queue = asyncio.Queue()
    _channels[task_id] = queue

    async def progress(stage: str, state: str, data: Dict[str, Any]) -> None:
        await queue.put({"stage": stage, "state": state, **data})

    async def worker() -> None:
        try:
            await pipeline.run(task_id, white_bytes, uv_bytes, store,
                               notes=notes, on_progress=progress)
        except Exception as exc:                    # 兜底：任何异常都要让前端收到结束事件
            log.exception("分析失败 task=%s", task_id)
            await queue.put({"stage": "failed", "state": "failed", "error": str(exc)})
            store.finish(task_id, "failed", {"task_id": task_id, "status": "failed",
                                             "notes_zh": ["分析过程出错，请重试。"],
                                             "notes_en": ["Analysis failed. Please try again."]})
        finally:
            await queue.put(None)
            # 客户端可能根本没来订阅（切后台、断网）。不清理的话队列会一直挂在
            # _channels 里泄漏内存，所以给一个宽限期后强制回收。
            async def _reap() -> None:
                await asyncio.sleep(120)
                _channels.pop(task_id, None)
            asyncio.create_task(_reap())

    background.add_task(worker)
    return {"task_id": task_id, "stream": f"/api/v1/tasks/{task_id}/stream",
            "result": f"/api/v1/tasks/{task_id}"}


# ------------------------------------------------------------------ stream

@app.get("/api/v1/tasks/{task_id}/stream")
async def stream(task_id: str) -> StreamingResponse:
    queue = _channels.get(task_id)
    if queue is None:
        rec = store.get(task_id)
        if rec is None:
            raise HTTPException(404, "任务不存在")

        async def replay():
            yield _sse({"stage": "done", "state": "done", "result": rec.get("result")})
        return StreamingResponse(replay(), media_type="text/event-stream")

    async def gen():
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=20.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"        # 反代常在 30–60s 掐断空闲连接
                    continue
                if item is None:
                    break
                yield _sse(item)
        finally:
            _channels.pop(task_id, None)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _sse(payload: Dict[str, Any]) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


# ------------------------------------------------------------------ results

@app.get("/api/v1/tasks/{task_id}")
async def get_task(task_id: str) -> JSONResponse:
    rec = store.get(task_id)
    if rec is None:
        raise HTTPException(404, "任务不存在或已过期")
    if rec["status"] == "running":
        return JSONResponse({"task_id": task_id, "status": "running"}, status_code=202)
    return JSONResponse(rec.get("result") or {"task_id": task_id, "status": rec["status"]})


@app.get("/api/v1/tasks/{task_id}/assets/{name}")
async def get_asset(task_id: str, name: str) -> FileResponse:
    p = store.get_asset(task_id, name)
    if p is None:
        raise HTTPException(404, "资源不存在")
    return FileResponse(p, headers={"Cache-Control": "private, max-age=86400"})


@app.delete("/api/v1/tasks/{task_id}")
async def delete_task(task_id: str) -> Dict[str, Any]:
    return {"deleted": store.delete(task_id)}


# ------------------------------------------------------------------ meta

@app.get("/api/v1/config")
async def frontend_config() -> Dict[str, Any]:
    cfg = grading_config()
    return {
        "default_locale": settings.default_locale,
        "grading_version": cfg.get("version"),
        "hidden_spread": cfg.get("hidden_spread", {}),
        "levels": [
            {"level": l["level"], "key": l["key"],
             "label_zh": l["label_zh"], "label_en": l["label_en"],
             "criteria_zh": " ".join(l["criteria_zh"].split()),
             "criteria_en": " ".join(l["criteria_en"].split()),
             "action_zh": l["action_zh"], "action_en": l["action_en"]}
            for l in cfg["levels"]
        ],
        "calibration_card_enabled": settings.enable_calibration_card,
    }


@app.get("/api/v1/health")
async def health() -> Dict[str, Any]:
    out: Dict[str, Any] = {"status": "ok", "model": settings.llm_model,
                           "base_url": settings.llm_base_url}
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get(settings.llm_base_url.rstrip("/") + "/models",
                            headers={"Authorization": f"Bearer {settings.llm_api_key}"})
            out["llm_reachable"] = r.status_code < 500
    except Exception as exc:
        out["llm_reachable"] = False
        out["llm_error"] = str(exc)[:200]
        out["status"] = "degraded"      # 服务仍可用，会降级为纯 CV 结果
    return out


# ------------------------------------------------------------------ static

WEB = BASE_DIR / "web"
if WEB.is_dir():
    app.mount("/", StaticFiles(directory=str(WEB), html=True), name="web")
