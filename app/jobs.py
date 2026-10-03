"""任务队列与执行器。

* 上传在路由层同步完成（写入 ``var/jobs/<id>/in/``），随后把任务丢进线程池。
* 每个任务有自己的取消事件，引擎层在文件之间检查它，做到「可取消」。
* 进度与状态实时写入 ``meta.json``，前端轮询 ``/api/jobs/{id}`` 即可；
  即使进程重启，历史记录仍然在。
* 运行中的任务保留在内存字典里，读取时优先取内存（更实时），否则读磁盘。
"""

from __future__ import annotations

import logging
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import quote

from . import config, convert, storage
from .engines import imageops, ocr, pdfops, runner

log = logging.getLogger("docpix")


class JobCanceled(Exception):
    """任务被用户取消。"""


# ------------------------------------------------------------------ 上下文
@dataclass
class JobContext:
    id: str
    kind: str
    op: str
    params: dict
    inputs: list[Path]
    paths: storage.JobPaths
    cancel_event: threading.Event = field(default_factory=threading.Event)
    record: dict = field(default_factory=dict)
    _io_lock: threading.Lock = field(default_factory=threading.Lock)
    _last_flush: float = 0.0

    # ---- 取消 ----
    def canceled(self) -> bool:
        return self.cancel_event.is_set()

    def checkpoint(self) -> None:
        if self.canceled():
            raise JobCanceled()

    # ---- 进度 ----
    def progress(self, value: float, message: str | None = None) -> None:
        self.checkpoint()
        with self._io_lock:
            current = float(self.record.get("progress", 0.0))
            new_value = max(current, min(1.0, max(0.0, float(value))))
            self.record["progress"] = new_value
            if message:
                self.record["message"] = str(message)
        self._flush()

    def log(self, message: str) -> None:
        with self._io_lock:
            self.record["message"] = str(message)
        self._flush()

    def _flush(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_flush < 0.4:
            return
        self._last_flush = now
        try:
            storage.write_record(self.record)
        except OSError as exc:
            # 曾经在这里静默吞掉 Windows 的 WinError 5，导致任务永远停在 running。
            # 现在至少留一条日志，方便定位。
            log.warning("任务 %s 记录写入失败：%s", self.id, exc)

    # ---- 输出 ----
    def collect_outputs(self) -> None:
        outputs: list[dict] = []
        for f in storage.iter_outputs(self.id):
            if not f.is_file():
                continue
            quoted = quote(f.name)
            outputs.append({
                "name": f.name,
                "size": f.stat().st_size,
                "kind": storage.kind_of(f),
                "url": f"/api/jobs/{self.id}/files/{quoted}",
                "preview_url": f"/api/jobs/{self.id}/preview/{quoted}",
            })
        with self._io_lock:
            self.record["outputs"] = outputs

    def finish(self, status: str, *, error: str | None = None, message: str | None = None) -> None:
        with self._io_lock:
            self.record["status"] = status
            self.record["finished_at"] = time.time()
            if status == "done":
                self.record["progress"] = 1.0
            if error:
                self.record["error"] = error
            if message:
                self.record["message"] = message
        self._flush(force=True)


# ------------------------------------------------------------------ 处理器
_IMAGE_PARAM_KEYS = {
    "quality", "format", "width", "height", "mode", "max_dim", "left", "top",
    "right", "bottom", "unit", "angle", "axis", "text", "position", "opacity",
    "font_size", "color", "margin", "scale", "direction", "gap", "background",
}


def _image_handler(ctx: JobContext) -> None:
    op = ctx.op
    out = ctx.paths.out_dir
    params = {k: v for k, v in ctx.params.items() if k in _IMAGE_PARAM_KEYS}
    background = imageops.parse_color(params.pop("background", None))

    if op == "combine":
        ctx.log("正在拼接图片…")
        dst = out / "combined.png"
        imageops.combine(
            ctx.inputs, dst,
            direction=params.get("direction", "vertical"),
            gap=int(params.get("gap", 0) or 0),
            background=background,
            quality=int(params.get("quality", 92)),
        )
        ctx.progress(1.0, "拼接完成")
        return

    if op == "watermark_image":
        if len(ctx.inputs) < 2:
            raise runner.EngineError(
                "图片水印需要至少 2 个文件：待处理的图片 + 最后 1 个水印图片。"
            )
        mark, targets = ctx.inputs[-1], ctx.inputs[:-1]
        total = len(targets)
        # 前端以毫米给边距，这里按 96 dpi 折算成像素
        margin_px = int(round(float(params.get("margin_mm", 5.0)) * 96 / 25.4))
        for i, src in enumerate(targets):
            ctx.checkpoint()
            ext = imageops.normalize_ext(src.suffix) or "png"
            dst = storage.unique_path(out, f"{src.stem}_watermark.{ext}")
            imageops.watermark_image(
                src, dst, mark,
                position=params.get("position", "bottom-right"),
                scale=float(params.get("scale", 0.2)),
                opacity=float(params.get("opacity", 0.6)),
                margin=margin_px,
                quality=int(params.get("quality", 92)),
            )
            ctx.progress((i + 1) / total, f"已处理 {i + 1}/{total}：{src.name}")
        return

    if op not in imageops._BATCH_OPS:
        raise runner.EngineError(f"不支持的图片操作：{op}")

    ctx.log(f"正在处理 {len(ctx.inputs)} 张图片…")

    def on_progress(value: float, _op=op) -> None:
        ctx.progress(value, f"图片 {op} 进度 {round(value * 100)}%")

    imageops.batch(ctx.inputs, out, op, params=params, progress=on_progress)


def _pdf_handler(ctx: JobContext) -> None:
    op = ctx.op
    out = ctx.paths.out_dir
    params = ctx.params
    ins = ctx.inputs
    first = ins[0]
    stem = first.stem

    def p(key: str, default=None):
        value = params.get(key, default)
        return default if value is None else value

    ctx.log(f"PDF 操作：{op}")

    if op == "merge":
        if len(ins) < 2:
            raise runner.EngineError("合并至少需要 2 个 PDF 文件。")
        dst = out / "merged.pdf"
        total = len(ins)
        # pypdf 合并本身很快，这里按文件数上报进度
        for i in range(total):
            ctx.checkpoint()
            ctx.progress((i + 1) / total * 0.9, f"合并 {i + 1}/{total}")
        pdfops.merge(ins, dst)
        ctx.progress(1.0, f"已合并 {total} 个文件")
        return

    if op == "split":
        # mode="n" 时页数既可能放在 ranges，也可能放在 n（前端两种都支持）
        step = p("ranges") or p("n")
        files = pdfops.split(first, out, mode=str(p("mode", "each")), ranges=step)
        ctx.progress(1.0, f"已拆分为 {len(files)} 个文件")
        return

    if op == "extract_pages":
        pdfops.extract_pages(first, out / f"{stem}_pages.pdf", str(p("pages", "")))
        return

    if op == "delete_pages":
        pdfops.delete_pages(first, out / f"{stem}_deleted.pdf", str(p("pages", "")))
        return

    if op == "reorder":
        pdfops.reorder(first, out / f"{stem}_reordered.pdf", str(p("order", "")))
        return

    if op == "rotate":
        pdfops.rotate(first, out / f"{stem}_rotated.pdf", int(float(p("angle", 90))), pages=p("pages"))
        return

    if op == "compress":
        info = pdfops.compress(first, out / f"{stem}_compressed.pdf", level=str(p("level", "medium")))
        ctx.log(
            f"压缩完成：{info['before'] / 1024:.0f} KB → {info['after'] / 1024:.0f} KB"
            f"（省 {info['saved_ratio'] * 100:.1f}%）"
        )
        return

    if op == "encrypt":
        password = str(p("user_password", "") or p("password", ""))
        if not password:
            raise runner.EngineError("加密需要设置打开密码。")
        pdfops.encrypt(
            first, out / f"{stem}_encrypted.pdf",
            user_password=password,
            owner_password=p("owner_password"),
            allow_print=bool(p("allow_print", True)),
            allow_copy=bool(p("allow_copy", True)),
            allow_modify=bool(p("allow_modify", False)),
        )
        return

    if op == "decrypt":
        pdfops.decrypt(first, out / f"{stem}_decrypted.pdf", str(p("password", "")))
        return

    if op == "page_numbers":
        pdfops.add_page_numbers(
            first, out / f"{stem}_numbered.pdf",
            template=str(p("template", "{page} / {total}")),
            position=str(p("position", "bottom-center")),
            font_size=int(p("font_size", 10) or 10),
            margin_mm=float(p("margin_mm", 12.0)),
            start_at=int(p("start_at", 1)),
        )
        return

    if op == "watermark_text":
        text = str(p("text", "")).strip()
        if not text:
            raise runner.EngineError("水印文字不能为空。")
        pdfops.add_watermark(
            first, out / f"{stem}_watermark.pdf", text=text,
            font_size=int(p("font_size", 48) or 48),
            opacity=float(p("opacity", 0.18)),
            angle=float(p("angle", 45.0)),
            color=imageops.parse_color(p("color", "#e11d48"), (230, 29, 72)),
            pages=p("pages"),
        )
        return

    if op == "watermark_image":
        if len(ins) < 2:
            raise runner.EngineError("图片水印需要 1 个 PDF + 最后 1 个水印图片。")
        mark = ins[-1]
        for i, src in enumerate(ins[:-1]):
            ctx.checkpoint()
            pdfops.add_image_watermark(
                src, storage.unique_path(out, f"{src.stem}_watermark.pdf"), mark,
                scale=float(p("scale", 0.2)),
                opacity=float(p("opacity", 0.35)),
                position=str(p("position", "bottom-right")),
                margin_mm=float(p("margin_mm", 10.0)),
            )
            ctx.progress((i + 1) / max(1, len(ins) - 1), f"已处理 {src.name}")
        return

    if op == "pdf_to_images":
        files = pdfops.to_images(
            first, out,
            dpi=int(p("dpi", 150)),
            fmt=str(p("format") or p("fmt") or "png"),
            pages=p("pages"),
            quality=int(p("quality", 90)),
        )
        ctx.progress(1.0, f"已导出 {len(files)} 张图片")
        return

    if op == "images_to_pdf":
        pdfops.images_to_pdf(
            ins, out / "images.pdf",
            page_size=str(p("page_size", "fit")),
            margin_mm=float(p("margin_mm", 0.0)),
            quality=int(p("quality", 92)),
        )
        return

    if op == "extract_text":
        chunks = pdfops.extract_text(first, pages=p("pages"))
        text = "\n\n".join(f"--- 第 {c['page']} 页 ---\n{c['text']}" for c in chunks)
        (out / f"{stem}.txt").write_text(text, encoding="utf-8")
        chars = len(text.strip())
        ctx.log(f"已抽取 {len(chunks)} 页，共 {chars} 个字符")
        if chars == 0:
            ctx.log("未抽取到文字：这可能是扫描件，请改用 OCR 功能。")
        return

    if op == "metadata":
        fields = {k: params.get(k) for k in ("title", "author", "subject", "keywords") if params.get(k)}
        if not fields:
            raise runner.EngineError("请至少填写一项元数据（title / author / subject / keywords）。")
        pdfops.set_metadata(first, out / f"{stem}_meta.pdf", **fields)
        return

    if op == "linearize":
        pdfops.linearize(first, out / f"{stem}_linearized.pdf")
        return

    if op == "repair":
        pdfops.repair(first, out / f"{stem}_repaired.pdf")
        return

    raise runner.EngineError(f"不支持的 PDF 操作：{op}")


def _convert_handler(ctx: JobContext) -> None:
    target = str(ctx.params.get("target") or "").strip().lstrip(".")
    if not target:
        raise runner.EngineError("请指定目标格式（target）。")

    plans = [convert.plan(src, target) for src in ctx.inputs]
    with ctx._io_lock:
        ctx.record["plan"] = [p.as_dict() for p in plans]
    warning = next((p.warning for p in plans if p.warning), None)
    if warning:
        ctx.log(warning)
    ctx.log(f"链路：{' | '.join(s.label for s in plans[0].steps)}")

    files = convert.execute(
        ctx.inputs, ctx.paths.out_dir, target,
        options=ctx.params,
        progress=lambda value: ctx.progress(value, f"转换进度 {round(value * 100)}%"),
        cancel_check=ctx.canceled,
    )
    ctx.progress(1.0, f"已完成 {len(files)} 个文件")


def _ocr_handler(ctx: JobContext) -> None:
    langs = ctx.params.get("languages") or []
    flags = {
        "deskew": bool(ctx.params.get("deskew", False)),
        "rotate_pages": bool(ctx.params.get("rotate_pages", False)),
        "force": bool(ctx.params.get("force", False)),
        "skip_text": bool(ctx.params.get("skip_text", False)),
        "optimize": int(ctx.params.get("optimize", 1) or 0),
    }
    ctx.log(f"OCR 语言：{'+'.join(langs) if langs else '默认'}")
    files = ocr.run(
        ctx.inputs, ctx.paths.out_dir,
        languages=langs or None,
        output_type=str(ctx.params.get("output_type", "pdf")),
        progress=lambda value: ctx.progress(value, f"OCR 进度 {round(value * 100)}%"),
        **flags,
    )
    ctx.progress(1.0, f"OCR 完成，共 {len(files)} 个文件")


HANDLERS: dict[str, Callable[[JobContext], None]] = {
    "image": _image_handler,
    "pdf": _pdf_handler,
    "convert": _convert_handler,
    "ocr": _ocr_handler,
}


# ------------------------------------------------------------------ 管理器
class JobManager:
    """线程池 + 内存态 + 落盘记录。"""

    def __init__(self, max_workers: int | None = None) -> None:
        self.max_workers = max_workers or config.MAX_CONCURRENT_JOBS
        self._pool = ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix="docpix-job",
        )
        self._active: dict[str, JobContext] = {}
        self._lock = threading.Lock()
        self._cleanup_stop = threading.Event()
        self._cleanup_thread: threading.Thread | None = None

    # ---- 生命周期 ----
    def start_cleanup(self, interval: int = 600) -> None:
        if self._cleanup_thread and self._cleanup_thread.is_alive():
            return

        def loop() -> None:
            while not self._cleanup_stop.wait(interval):
                try:
                    storage.cleanup()
                except Exception:
                    continue

        self._cleanup_thread = threading.Thread(target=loop, name="docpix-cleanup", daemon=True)
        self._cleanup_thread.start()

    def shutdown(self) -> None:
        self._cleanup_stop.set()
        self._pool.shutdown(wait=False, cancel_futures=True)

    # ---- 提交 ----
    def submit(self, job_id: str, kind: str, op: str, params: dict,
               inputs: list[Path]) -> dict:
        if kind not in HANDLERS:
            raise runner.EngineError(f"未知的任务类型：{kind}")
        p = storage.ensure_job(job_id)
        record = {
            "id": job_id,
            "kind": kind,
            "op": op,
            "status": "pending",
            "progress": 0.0,
            "message": "排队中…",
            "error": None,
            "created_at": time.time(),
            "started_at": None,
            "finished_at": None,
            "params": _public_params(params),
            "inputs": [{"name": f.name, "size": f.stat().st_size} for f in inputs],
            "outputs": [],
            "plan": None,
        }
        ctx = JobContext(id=job_id, kind=kind, op=op, params=dict(params),
                         inputs=list(inputs), paths=p, record=record)
        with self._lock:
            self._active[job_id] = ctx
        storage.write_record(record)
        self._pool.submit(self._run, ctx)
        return record

    def _run(self, ctx: JobContext) -> None:
        ctx.record["status"] = "running"
        ctx.record["started_at"] = time.time()
        ctx.record["message"] = "开始执行…"
        ctx._flush(force=True)
        try:
            ctx.checkpoint()
            HANDLERS[ctx.kind](ctx)
            ctx.checkpoint()
            ctx.collect_outputs()
            if not ctx.record.get("outputs"):
                raise runner.EngineError("任务没有产生任何输出文件。")
            ctx.finish("done", message=ctx.record.get("message") or "完成")
        except JobCanceled:
            ctx.finish("canceled", message="任务已取消")
        except runner.EngineError as exc:
            ctx.finish("error", error=str(exc), message="执行失败")
        except Exception as exc:  # pragma: no cover - 兜底
            ctx.finish("error", error=f"{type(exc).__name__}: {exc}", message="执行失败")
            traceback.print_exc()
        finally:
            with self._lock:
                self._active.pop(ctx.id, None)
            ctx._flush(force=True)

    # ---- 查询 ----
    def get(self, job_id: str) -> dict | None:
        with self._lock:
            ctx = self._active.get(job_id)
        if ctx is not None:
            with ctx._io_lock:
                return dict(ctx.record)
        record = storage.read_record(job_id)
        return record

    def list(self, limit: int = 100) -> list[dict]:
        items = storage.list_records(limit)
        # 用内存态覆盖磁盘上的旧状态，保证 running 任务进度是最新的
        with self._lock:
            active = dict(self._active)
        if active:
            by_id = {r["id"]: r for r in items}
            for job_id, ctx in active.items():
                with ctx._io_lock:
                    by_id[job_id] = dict(ctx.record)
            items = sorted(by_id.values(), key=lambda r: r.get("created_at", 0), reverse=True)
        return items[:limit]

    # ---- 取消 / 删除 ----
    def cancel(self, job_id: str) -> bool:
        with self._lock:
            ctx = self._active.get(job_id)
        if ctx is not None:
            ctx.cancel_event.set()
            ctx.log("收到取消请求…")
            return True
        record = storage.read_record(job_id)
        if record and record.get("status") in ("pending", "running"):
            record["status"] = "canceled"
            record["finished_at"] = time.time()
            record["message"] = "任务已取消"
            storage.write_record(record)
            return True
        return False

    def delete(self, job_id: str) -> bool:
        self.cancel(job_id)
        with self._lock:
            self._active.pop(job_id, None)
        return storage.delete_job(job_id)

    def stats(self) -> dict:
        with self._lock:
            running = sum(1 for c in self._active.values() if c.record.get("status") == "running")
            queued = len(self._active) - running
        return {
            "active": len(self._active),
            "running": running,
            "queued": max(0, queued),
            "max_workers": self.max_workers,
        }


def _public_params(params: dict) -> dict:
    """记录里不回显密码类字段。"""
    hidden = {"user_password", "owner_password", "password"}
    return {k: ("***" if k in hidden and v else v) for k, v in params.items()}


#: 全局单例（由 app.main 在启动时创建/关闭）
manager = JobManager()


__all__ = ["JobCanceled", "JobContext", "JobManager", "manager", "HANDLERS"]
