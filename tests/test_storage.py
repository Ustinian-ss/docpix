"""存储层测试：文件名清洗、上传落盘、路径穿越防护、过期清理。"""

from __future__ import annotations

import io
import time
from pathlib import Path

import pytest

from app import config, storage


def test_safe_name_strips_path_and_illegal_chars():
    assert storage.safe_name("../../etc/passwd") == "passwd"
    assert storage.safe_name(r"..\..\windows\system32\cmd.exe") == "cmd.exe"
    assert storage.safe_name('a<b>c:d"e|f?g*h.txt') == "a_b_c_d_e_f_g_h.txt"
    assert storage.safe_name("   ") == "file"
    assert storage.safe_name("") == "file"
    assert storage.safe_name("con.txt") == "_con.txt"
    assert storage.safe_name("照片.png") == "照片.png"


def test_safe_name_keeps_extension_when_truncating():
    name = "x" * 300 + ".docx"
    clean = storage.safe_name(name)
    assert clean.endswith(".docx")
    assert len(clean) <= storage.MAX_NAME_LENGTH


def test_new_id_and_paths_are_under_jobs_dir():
    job_id = storage.new_id("image")
    assert job_id.startswith("image-")
    p = storage.ensure_job(job_id)
    assert p.in_dir.is_dir() and p.out_dir.is_dir()
    assert config.JOBS_DIR in p.root.parents
    storage.delete_job(job_id)


def test_save_upload_and_unique_path():
    job_id = storage.new_id("t")
    try:
        p1, n1 = storage.save_upload(job_id, "a.txt", io.BytesIO(b"hello"))
        p2, n2 = storage.save_upload(job_id, "a.txt", io.BytesIO(b"world!"))
        assert n1 == 5 and n2 == 6
        assert p1.name == "a.txt" and p2.name == "a (2).txt"
        assert p1.read_bytes() == b"hello"
    finally:
        storage.delete_job(job_id)


def test_save_upload_rejects_empty_and_oversize():
    job_id = storage.new_id("t")
    try:
        with pytest.raises(ValueError):
            storage.save_upload(job_id, "empty.txt", io.BytesIO(b""))
        with pytest.raises(ValueError):
            storage.save_upload(job_id, "big.bin", io.BytesIO(b"x" * 100), max_bytes=10)
        # 失败的上传不应留下文件
        assert list(storage.paths(job_id).in_dir.iterdir()) == []
    finally:
        storage.delete_job(job_id)


def test_write_read_list_and_delete_record():
    job_id = storage.new_id("t")
    try:
        record = {"id": job_id, "status": "done", "created_at": time.time(),
                  "finished_at": time.time(), "kind": "image", "op": "convert"}
        storage.write_record(record)
        loaded = storage.read_record(job_id)
        assert loaded and loaded["status"] == "done"
        ids = [r["id"] for r in storage.list_records(10)]
        assert job_id in ids
        assert storage.delete_job(job_id) is True
        assert storage.read_record(job_id) is None
    finally:
        storage.delete_job(job_id)


def test_write_record_survives_concurrent_reads():
    """回归：Windows 上读侧持句柄会让 os.replace 报 WinError 5。

    以前 ``_flush`` 静默吞掉这个错误，任务会永远停在 running/0%，
    这里保证「一边轮询读、一边写」时最后一次写入一定落盘，且不留临时文件。
    """
    import threading

    job_id = storage.new_id("race")
    stop = threading.Event()

    def reader() -> None:
        while not stop.is_set():
            storage.read_record(job_id)
            storage.list_records(20)

    try:
        storage.ensure_job(job_id)
        th = threading.Thread(target=reader, daemon=True)
        th.start()
        try:
            for i in range(120):
                storage.write_record({
                    "id": job_id, "status": "running", "progress": i / 120,
                    "message": "写" * 50, "created_at": time.time(), "outputs": [],
                })
        finally:
            stop.set()
            th.join(timeout=2)

        last = storage.read_record(job_id)
        assert last is not None and last["progress"] == pytest.approx(119 / 120)
        assert list(storage.paths(job_id).root.glob("*.tmp")) == []
    finally:
        storage.delete_job(job_id)


def test_find_output_blocks_path_traversal():
    job_id = storage.new_id("t")
    try:
        p = storage.ensure_job(job_id)
        (p.out_dir / "ok.pdf").write_bytes(b"%PDF-1.4")
        (p.root / "secret.txt").write_text("secret", encoding="utf-8")
        assert storage.find_output(job_id, "ok.pdf") is not None
        assert storage.find_output(job_id, "../secret.txt") is None
        assert storage.find_output(job_id, "..\\secret.txt") is None
        assert storage.find_output(job_id, "nope.pdf") is None
    finally:
        storage.delete_job(job_id)


def test_kind_of():
    assert storage.kind_of("a.PNG") == "image"
    assert storage.kind_of("a.pdf") == "pdf"
    assert storage.kind_of("a.md") == "text"
    assert storage.kind_of("a.zip") == "archive"
    assert storage.kind_of("a.bin") == "other"


def test_cleanup_removes_expired_jobs():
    job_id = storage.new_id("t")
    p = storage.ensure_job(job_id)
    (p.out_dir / "x.txt").write_text("x", encoding="utf-8")
    old = time.time() - 10_000
    storage.write_record({"id": job_id, "status": "done", "created_at": old,
                          "finished_at": old, "kind": "image", "op": "convert"})
    stats = storage.cleanup(job_seconds=3600, output_seconds=3600)
    assert stats["removed_jobs"] >= 1
    assert not p.root.exists()


def test_cleanup_keeps_fresh_and_running_jobs():
    fresh = storage.new_id("t")
    storage.ensure_job(fresh)
    storage.write_record({"id": fresh, "status": "running", "created_at": time.time(),
                          "finished_at": None, "kind": "pdf", "op": "merge"})
    try:
        storage.cleanup(job_seconds=3600, output_seconds=3600)
        assert storage.paths(fresh).root.exists()
    finally:
        storage.delete_job(fresh)
