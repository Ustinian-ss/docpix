"""API 数据模型（Pydantic v2）。

这些模型同时承担两个职责：

1. 给 FastAPI 的 ``/openapi.json`` 提供准确的请求/响应结构；
2. 给前端一份「参数白名单 + 取值范围」，非法参数在进入引擎之前就被拦下。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import config


class JobStatus(str, Enum):
    pending = "pending"
    running = "running"
    done = "done"
    error = "error"
    canceled = "canceled"


JobKind = Literal["image", "pdf", "convert", "ocr"]


class JobInput(BaseModel):
    name: str
    size: int


class JobOutput(BaseModel):
    name: str
    size: int
    kind: str
    url: str
    preview_url: str


class JobRecord(BaseModel):
    id: str
    kind: str
    op: str
    status: JobStatus = JobStatus.pending
    progress: float = 0.0
    message: str = ""
    error: str | None = None
    created_at: float
    started_at: float | None = None
    finished_at: float | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    inputs: list[JobInput] = Field(default_factory=list)
    outputs: list[JobOutput] = Field(default_factory=list)
    plan: list[dict[str, Any]] | None = None


class TaskParams(BaseModel):
    """所有操作共用的参数集合。

    不同 ``op`` 只用到其中一部分；多余字段允许透传（``extra="allow"``），
    方便将来加新参数而不用改模型。
    """

    model_config = ConfigDict(extra="allow")

    # ---- 通用 ----
    quality: int = 92
    format: str | None = None

    # ---- 图片 ----
    width: int | None = None
    height: int | None = None
    mode: str = "fit"
    max_dim: int | None = None
    left: int = 0
    top: int = 0
    right: int | None = None
    bottom: int | None = None
    unit: str = "px"
    angle: float = 0.0
    axis: str = "horizontal"
    text: str = ""
    position: str = "bottom-right"
    opacity: float = 0.85
    font_size: int = 0
    color: str = "#ffffff"
    margin: int = 24
    margin_mm: float = 10.0
    scale: float = 0.2
    direction: str = "vertical"
    gap: int = 0
    background: str = "#ffffff"

    # ---- PDF ----
    pages: str | None = None
    order: str | None = None
    ranges: str | None = None
    level: str = "medium"
    password: str = ""
    user_password: str | None = None
    owner_password: str | None = None
    allow_print: bool = True
    allow_copy: bool = True
    allow_modify: bool = False
    template: str = "{page} / {total}"
    start_at: int = 1
    dpi: int = 150
    page_size: str = "fit"
    title: str | None = None
    author: str | None = None
    subject: str | None = None
    keywords: str | None = None

    # ---- 转换 / OCR ----
    target: str | None = None
    languages: list[str] = Field(default_factory=list)
    deskew: bool = False
    force: bool = False
    skip_text: bool = False
    rotate_pages: bool = False
    #: ocrmypdf --optimize 的档位：0 = 不优化，1 = 默认，2/3 = 更激进（0-3）
    optimize: int = Field(1, ge=0, le=3)
    output_type: str = "pdf"

    @field_validator("quality")
    @classmethod
    def _quality(cls, v: int) -> int:
        if not 1 <= int(v) <= 100:
            raise ValueError("quality 必须在 1–100 之间")
        return int(v)

    @field_validator("opacity")
    @classmethod
    def _opacity(cls, v: float) -> float:
        if not 0.0 <= float(v) <= 1.0:
            raise ValueError("opacity 必须在 0–1 之间")
        return float(v)

    @field_validator("dpi")
    @classmethod
    def _dpi(cls, v: int) -> int:
        if not 30 <= int(v) <= 1200:
            raise ValueError("dpi 必须在 30–1200 之间")
        return int(v)

    @field_validator("scale")
    @classmethod
    def _scale(cls, v: float) -> float:
        if not 0.01 <= float(v) <= 1.0:
            raise ValueError("scale 必须在 0.01–1.0 之间")
        return float(v)

    @field_validator("margin", "gap", "font_size", "start_at")
    @classmethod
    def _non_negative(cls, v: int) -> int:
        if int(v) < 0:
            raise ValueError("该参数不能为负数")
        return int(v)

    @field_validator("languages")
    @classmethod
    def _langs(cls, v: list[str]) -> list[str]:
        for code in v:
            if code not in config.OCR_LANGS:
                raise ValueError(f"不支持的 OCR 语言：{code}")
        return v

    def clean(self) -> dict[str, Any]:
        """去掉 None，转成普通 dict 交给引擎层。"""
        return {k: v for k, v in self.model_dump().items() if v is not None}


class EngineInfo(BaseModel):
    key: str
    name: str
    license: str
    homepage: str
    purpose: str
    required: bool
    available: bool
    path: str | None = None
    languages: list[str] | None = None
    tessdata: str | None = None
    version: str | None = None
    hint: str | None = None


class Limits(BaseModel):
    max_upload_bytes: int = config.MAX_UPLOAD_BYTES
    max_batch_files: int = config.MAX_BATCH_FILES
    max_concurrent_jobs: int = config.MAX_CONCURRENT_JOBS


class Retention(BaseModel):
    job_seconds: int = config.JOB_RETENTION_SECONDS
    output_seconds: int = config.OUTPUT_RETENTION_SECONDS


class SystemInfo(BaseModel):
    version: str
    python: str
    platform: str
    license: str
    license_note: str
    engines: list[EngineInfo]
    limits: Limits
    retention: Retention


class FormatsInfo(BaseModel):
    image_input: list[str]
    image_output: list[str]
    office_targets: list[str]
    pandoc_targets: list[str]
    convert_matrix: dict[str, list[str]]
    image_ops: list[str]
    pdf_ops: list[str]
    ocr_languages: dict[str, str]
    installed_ocr_languages: list[str]
    ocr_output_types: list[str]


class ImageInfo(BaseModel):
    file: str
    size: int
    width: int
    height: int
    mode: str
    format: str | None = None
    has_alpha: bool = False
    frames: int = 1
    animated: bool = False
    megapixels: float = 0.0
    aspect: float | None = None
    dpi: list[float] | None = None
    exif: dict[str, str] | None = None


class PdfInfo(BaseModel):
    file: str
    size: int
    pages: int = 0
    encrypted: bool = False
    metadata: dict[str, str] = Field(default_factory=dict)
    page_size_pt: list[float] | None = None
    page_size_mm: list[float] | None = None
    error: str | None = None


class InspectResult(BaseModel):
    name: str
    size: int
    kind: Literal["image", "pdf", "text", "other"]
    image: ImageInfo | None = None
    pdf: PdfInfo | None = None
    preview_url: str | None = None
    text_preview: str | None = None


class OkResponse(BaseModel):
    ok: bool = True
    message: str | None = None
    detail: dict[str, Any] | None = None


__all__ = [
    "JobStatus", "JobKind", "JobInput", "JobOutput", "JobRecord", "TaskParams",
    "EngineInfo", "Limits", "Retention", "SystemInfo", "FormatsInfo",
    "ImageInfo", "PdfInfo", "InspectResult", "OkResponse",
]
