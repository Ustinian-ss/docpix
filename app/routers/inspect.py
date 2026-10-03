"""文件探测路由：``POST /api/inspect``。

上传单个文件，返回图片 / PDF / 文本的基本信息，供前端在提交任务前展示。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from .. import config, storage
from ..engines import imageops, pdfops
from ..models import ImageInfo, InspectResult, PdfInfo

router = APIRouter(prefix="/api", tags=["inspect"])


def _detect(name: str) -> str:
    ext = Path(name).suffix.lower()
    if ext in config.IMAGE_EXTS:
        return "image"
    if ext in config.PDF_EXTS:
        return "pdf"
    if ext in {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".html", ".htm",
               ".xml", ".log", ".rst", ".org", ".tex"}:
        return "text"
    return "other"


@router.post("/inspect", response_model=InspectResult)
async def inspect(file: UploadFile = File(...)) -> InspectResult:
    name = storage.safe_name(file.filename or "file")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="上传的文件是空的。")
    if len(data) > min(config.MAX_UPLOAD_BYTES, 256 * 1024 * 1024):
        raise HTTPException(status_code=413, detail="用于探测的文件不能超过 256 MB。")

    kind = _detect(name)
    tmp_dir = config.temp_dir()
    tmp = tmp_dir / name
    try:
        tmp.write_bytes(data)
        result = InspectResult(name=name, size=len(data), kind=kind)  # type: ignore[arg-type]

        if kind == "image":
            result.image = ImageInfo(**imageops.info(tmp))
        elif kind == "pdf":
            result.pdf = PdfInfo(**pdfops.info(tmp))
            result.preview_url = None
        elif kind == "text":
            try:
                text = tmp.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            result.text_preview = text[:4000]
        return result
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=400, detail=f"无法解析该文件：{exc}") from exc
    finally:
        try:
            tmp.unlink(missing_ok=True)
            tmp_dir.rmdir()
        except OSError:
            pass


@router.get("/inspect/meta")
def inspect_meta() -> dict:
    """前端用来渲染上传区提示的静态信息。"""
    return {
        "max_upload_bytes": config.MAX_UPLOAD_BYTES,
        "max_batch_files": config.MAX_BATCH_FILES,
        "image_exts": sorted(config.IMAGE_EXTS),
        "doc_exts": sorted(config.DOC_EXTS),
        "pdf_exts": sorted(config.PDF_EXTS),
    }


__all__ = ["router"]
