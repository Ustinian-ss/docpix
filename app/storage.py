"""任务与文件的落盘存储。

目录布局（全部落在 F 盘项目目录内）::

    var/jobs/<job-id>/
        meta.json          任务记录（状态、进度、输入、输出）
        in/                上传的原始文件
        out/               转换产物（对外下载的就是这里的文件）

设计原则：
* 任何用户提供的文件名都经过 :func:`safe_name` 清洗，绝不参与路径拼接，
  避免目录穿越（``..\\..\\``）与非法字符。
* 任务目录是唯一删除单位，过期清理直接整目录删除。
* 记录写入用「临时文件 + 原子替换」，避免进程中断留下半个 JSON。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator

from . import config

#: Windows 非法字符 + 路径分隔符
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_SPACES = re.compile(r"\s+")
#: Windows 保留设备名
_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}

MAX_NAME_LENGTH = 120


def safe_name(name: str, fallback: str = "file") -> str:
    """把任意字符串清洗成安全的文件名（保留扩展名）。"""
    base = Path(str(name or "")).name
    base = _ILLEGAL.sub("_", base).strip(" .")
    base = _SPACES.sub(" ", base)
    if not base:
        base = fallback
    stem, dot, suffix = base.rpartition(".")
    if not dot:
        stem, suffix = base, ""
    stem = stem or fallback
    if stem.lower() in _RESERVED:
        stem = f"_{stem}"
    room = MAX_NAME_LENGTH - (len(suffix) + 1 if suffix else 0)
    stem = stem[: max(1, room)]
    return f"{stem}.{suffix}" if suffix else stem


def new_id(prefix: str = "job") -> str:
    return f"{prefix}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


def unique_path(directory: Path, name: str) -> Path:
    """在目录内生成不冲突的路径：``a.pdf`` / ``a (2).pdf`` / …"""
    directory.mkdir(parents=True, exist_ok=True)
    clean = safe_name(name)
    candidate = directory / clean
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    for i in range(2, 1000):
        candidate = directory / f"{stem} ({i}){suffix}"
        if not candidate.exists():
            return candidate
    return directory / f"{stem}-{uuid.uuid4().hex[:6]}{suffix}"


@dataclass(frozen=True)
class JobPaths:
    id: str
    root: Path
    in_dir: Path
    out_dir: Path
    meta: Path


def paths(job_id: str) -> JobPaths:
    root = config.JOBS_DIR / job_id
    return JobPaths(id=job_id, root=root, in_dir=root / "in", out_dir=root / "out",
                    meta=root / "meta.json")


def ensure_job(job_id: str) -> JobPaths:
    p = paths(job_id)
    p.in_dir.mkdir(parents=True, exist_ok=True)
    p.out_dir.mkdir(parents=True, exist_ok=True)
    return p


def save_upload(job_id: str, filename: str, stream: BinaryIO,
                *, max_bytes: int | None = None, chunk: int = 1024 * 1024) -> tuple[Path, int]:
    """把上传流写入 ``in/``，返回 ``(路径, 字节数)``；超限抛 ``ValueError``。"""
    limit = max_bytes or config.MAX_UPLOAD_BYTES
    p = ensure_job(job_id)
    dst = unique_path(p.in_dir, filename)
    written = 0
    try:
        with open(dst, "wb") as fh:
            while True:
                block = stream.read(chunk)
                if not block:
                    break
                written += len(block)
                if written > limit:
                    raise ValueError("文件超过单文件大小上限")
                fh.write(block)
    except Exception:
        dst.unlink(missing_ok=True)
        raise
    if written == 0:
        dst.unlink(missing_ok=True)
        raise ValueError("上传的文件是空的")
    return dst, written


# ------------------------------------------------------------------ 记录读写
#: Windows 上 ``os.replace`` 会因目标文件被别的线程/进程打开而报 WinError 5；
#: 读侧（API 轮询）几乎总会和写侧重叠，所以必须重试。
_REPLACE_TRIES = 10
_REPLACE_DELAY = 0.02


def _replace_retry(tmp: Path, dst: Path) -> None:
    """原子替换，带退避重试（主要针对 Windows 的文件占用）。"""
    last: OSError | None = None
    for attempt in range(_REPLACE_TRIES):
        try:
            os.replace(tmp, dst)
            return
        except OSError as exc:  # PermissionError / 偶发的共享冲突
            last = exc
            time.sleep(_REPLACE_DELAY * (attempt + 1))
    assert last is not None
    raise last


def write_record(record: dict) -> None:
    """把任务记录写回 meta.json（临时文件 + 原子替换）。

    临时文件名带线程后缀，避免同一任务的两次写入互相覆盖；
    替换失败时清掉临时文件，不让它留在任务目录里。
    """
    p = ensure_job(record["id"])
    tmp = p.meta.with_name(f"{p.meta.name}.{os.getpid()}-{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        _replace_retry(tmp, p.meta)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:  # pragma: no cover - 清理失败不影响主流程
                pass


def read_record(job_id: str) -> dict | None:
    p = paths(job_id)
    for attempt in range(3):
        if not p.meta.is_file():
            return None
        try:
            return json.loads(p.meta.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        except OSError:
            time.sleep(_REPLACE_DELAY * (attempt + 1))
    return None


def list_records(limit: int = 100) -> list[dict]:
    if not config.JOBS_DIR.is_dir():
        return []
    items: list[dict] = []
    for meta in config.JOBS_DIR.glob("*/meta.json"):
        try:
            items.append(json.loads(meta.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    items.sort(key=lambda r: r.get("created_at", 0), reverse=True)
    return items[:limit]


def delete_job(job_id: str) -> bool:
    p = paths(job_id)
    if not p.root.exists():
        return False
    shutil.rmtree(p.root, ignore_errors=True)
    return True


def dir_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                continue
    return total


def iter_outputs(job_id: str) -> Iterator[Path]:
    p = paths(job_id)
    if not p.out_dir.is_dir():
        return
    yield from sorted(f for f in p.out_dir.iterdir() if f.is_file())


def find_output(job_id: str, name: str) -> Path | None:
    """按文件名在任务输出目录里定位文件（防目录穿越）。"""
    p = paths(job_id)
    clean = Path(str(name)).name
    if not clean or clean != str(name) or clean in (".", ".."):
        return None
    candidate = p.out_dir / clean
    try:
        candidate.resolve().relative_to(p.out_dir.resolve())
    except (ValueError, OSError):
        return None
    return candidate if candidate.is_file() else None


def kind_of(path: Path | str) -> str:
    ext = Path(path).suffix.lower()
    if ext in config.IMAGE_EXTS:
        return "image"
    if ext in config.PDF_EXTS:
        return "pdf"
    if ext in (".txt", ".md", ".csv", ".tsv", ".json", ".html", ".htm", ".xml", ".log"):
        return "text"
    if ext in (".zip",):
        return "archive"
    return "other"


# ------------------------------------------------------------------ 清理
def cleanup(*, job_seconds: int | None = None, output_seconds: int | None = None) -> dict:
    """删除过期任务目录，返回统计信息。"""
    job_ttl = job_seconds if job_seconds is not None else config.JOB_RETENTION_SECONDS
    out_ttl = output_seconds if output_seconds is not None else config.OUTPUT_RETENTION_SECONDS
    now = time.time()
    removed_jobs = 0
    removed_outputs = 0
    freed = 0

    if not config.JOBS_DIR.is_dir():
        return {"removed_jobs": 0, "removed_outputs": 0, "freed_bytes": 0}

    for job_root in config.JOBS_DIR.iterdir():
        if not job_root.is_dir():
            continue
        record = read_record(job_root.name) or {}
        finished = record.get("finished_at") or record.get("created_at") or 0
        status = record.get("status")
        out_dir = job_root / "out"

        # 产物按 output_ttl 单独过期
        if out_dir.is_dir() and finished and now - float(finished) > out_ttl:
            if any(out_dir.iterdir()):
                removed_outputs += 1
                freed += dir_size(out_dir)
                shutil.rmtree(out_dir, ignore_errors=True)

        # 任务记录（含上传文件）按 job_ttl 过期；运行中的任务不动
        stale = finished and now - float(finished) > job_ttl
        if status in ("pending", "running") and now - float(record.get("created_at", now)) < job_ttl:
            stale = False
        if stale:
            freed += dir_size(job_root)
            shutil.rmtree(job_root, ignore_errors=True)
            removed_jobs += 1

    return {"removed_jobs": removed_jobs, "removed_outputs": removed_outputs, "freed_bytes": freed}


__all__ = [
    "JobPaths", "safe_name", "new_id", "unique_path", "paths", "ensure_job",
    "save_upload", "write_record", "read_record", "list_records", "delete_job",
    "dir_size", "iter_outputs", "find_output", "kind_of", "cleanup",
]
