"""任务路由：创建、查询、取消、下载、预览。

上传与解密都在这里完成：

1. ``POST /api/jobs`` 先落盘上传文件（``in/``），再交给 :mod:`app.jobs` 的
   线程池执行，立即返回 ``202`` 与任务 id；
2. 前端轮询 ``GET /api/jobs/{id}`` 拿进度；
3. 产物通过 ``/files/{name}`` 下载、``/preview/{name}`` 预览。
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, Response

from .. import config, convert, storage
from ..engines import imageops, pdfops, runner
from ..jobs import manager
from ..models import JobRecord, OkResponse, TaskParams

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

_CAPS = convert.capability_summary()
ALLOWED_OPS: dict[str, set[str]] = {
    "image": set(_CAPS["image_ops"]),
    "pdf": set(_CAPS["pdf_ops"]),
    "convert": {"convert"},
    "ocr": {"ocr"},
}

_MEDIA_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".avif": "image/avif", ".heic": "image/heic",
    ".gif": "image/gif", ".bmp": "image/bmp", ".tif": "image/tiff",
    ".tiff": "image/tiff", ".ico": "image/x-icon", ".svg": "image/svg+xml",
    ".pdf": "application/pdf", ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8", ".csv": "text/csv; charset=utf-8",
    ".json": "application/json", ".html": "text/html; charset=utf-8",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".epub": "application/epub+zip",
    ".zip": "application/zip",
}


def _media_type(path: Path) -> str:
    return _MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")


def _parse_params(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"params 不是合法 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="params 必须是一个 JSON 对象。")
    try:
        return TaskParams(**data).clean()
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"参数不合法：{exc}") from exc


@router.post("", status_code=202, response_model=JobRecord)
async def create_job(
    kind: str = Form(..., description="image | pdf | convert | ocr"),
    op: str = Form(..., description="具体操作，见 /api/system/formats"),
    params: str | None = Form(None, description="JSON 字符串"),
    files: list[UploadFile] = File(..., description="按顺序上传的文件"),
) -> JobRecord:
    kind = (kind or "").strip().lower()
    op = (op or "").strip().lower()
    if kind not in ALLOWED_OPS:
        raise HTTPException(status_code=400, detail=f"未知任务类型：{kind}")
    if op not in ALLOWED_OPS[kind]:
        raise HTTPException(
            status_code=400,
            detail=f"{kind} 不支持操作 {op}；可选：{', '.join(sorted(ALLOWED_OPS[kind]))}",
        )
    if not files:
        raise HTTPException(status_code=400, detail="至少需要一个文件。")
    if len(files) > config.MAX_BATCH_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"单次最多 {config.MAX_BATCH_FILES} 个文件，当前 {len(files)} 个。",
        )

    parsed = _parse_params(params)
    if kind == "convert" and not parsed.get("target"):
        raise HTTPException(status_code=422, detail="转换任务必须提供 target（目标格式）。")
    if kind == "image" and op == "watermark_image" and len(files) < 2:
        raise HTTPException(status_code=422, detail="图片水印需要：待处理图片 + 最后 1 个水印图片。")
    if kind == "pdf" and op in ("merge", "images_to_pdf") and len(files) < 2:
        raise HTTPException(status_code=422, detail=f"{op} 至少需要 2 个文件。")

    job_id = storage.new_id(kind)
    storage.ensure_job(job_id)
    saved: list[Path] = []
    try:
        for upload in files:
            name = storage.safe_name(upload.filename or "file")
            path, _size = storage.save_upload(job_id, name, upload.file)
            saved.append(path)
    except ValueError as exc:
        storage.delete_job(job_id)
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except Exception as exc:
        storage.delete_job(job_id)
        raise HTTPException(status_code=500, detail=f"保存上传文件失败：{exc}") from exc

    try:
        record = manager.submit(job_id, kind, op, parsed, saved)
    except runner.EngineError as exc:
        storage.delete_job(job_id)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JobRecord(**record)


@router.get("", response_model=list[JobRecord])
def list_jobs(limit: int = Query(50, ge=1, le=500)) -> list[JobRecord]:
    return [JobRecord(**r) for r in manager.list(limit)]


@router.get("/stats")
def job_stats() -> dict:
    return manager.stats()


@router.get("/{job_id}", response_model=JobRecord)
def get_job(job_id: str) -> JobRecord:
    record = manager.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="任务不存在或已被清理。")
    return JobRecord(**record)


@router.post("/{job_id}/cancel", response_model=OkResponse)
def cancel_job(job_id: str) -> OkResponse:
    record = manager.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="任务不存在或已被清理。")
    ok = manager.cancel(job_id)
    return OkResponse(ok=ok, message="已发送取消请求" if ok else "任务已经结束")


@router.delete("/{job_id}", response_model=OkResponse)
def delete_job(job_id: str) -> OkResponse:
    if not manager.delete(job_id):
        raise HTTPException(status_code=404, detail="任务不存在或已被清理。")
    return OkResponse(ok=True, message="已删除任务及其产物")


@router.get("/{job_id}/files/{name}")
def download_output(job_id: str, name: str) -> FileResponse:
    path = storage.find_output(job_id, name)
    if path is None:
        raise HTTPException(status_code=404, detail="文件不存在或已被清理。")
    return FileResponse(path, media_type=_media_type(path), filename=path.name)


@router.get("/{job_id}/preview/{name}")
def preview_output(
    job_id: str,
    name: str,
    page: int = Query(1, ge=1, le=500),
    width: int = Query(1000, ge=200, le=2400),
) -> Response:
    path = storage.find_output(job_id, name)
    if path is None:
        raise HTTPException(status_code=404, detail="文件不存在或已被清理。")

    kind = storage.kind_of(path)
    if kind == "pdf":
        try:
            png = pdfops.render_preview(path, page=page, width=width)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"无法渲染 PDF 预览：{exc}") from exc
        return Response(content=png, media_type="image/png",
                        headers={"Cache-Control": "no-store"})
    if kind == "image":
        try:
            data, mime = imageops.preview_bytes(path, max_dim=width)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"无法生成图片预览：{exc}") from exc
        return Response(content=data, media_type=mime, headers={"Cache-Control": "no-store"})
    if kind == "text":
        text = path.read_text(encoding="utf-8", errors="replace")
        return PlainTextResponse(text[:200_000])

    return FileResponse(path, media_type=_media_type(path),
                        headers={"Content-Disposition": f'inline; filename="{path.name}"'})


__all__ = ["router", "ALLOWED_OPS"]
