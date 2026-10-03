"""HTTP API 测试（FastAPI TestClient，走真实的上传 → 任务 → 下载链路）。"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest


def wait_done(client, job_id: str, timeout: float = 60.0) -> dict:
    deadline = time.time() + timeout
    record = {}
    while time.time() < deadline:
        r = client.get(f"/api/jobs/{job_id}")
        assert r.status_code == 200, r.text
        record = r.json()
        if record["status"] in ("done", "error", "canceled"):
            return record
        time.sleep(0.2)
    raise AssertionError(f"任务超时未结束：{record}")


def upload(path: Path, field: str = "files"):
    suffix = path.suffix.lower().lstrip(".")
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
            "pdf": "application/pdf"}.get(suffix, "application/octet-stream")
    return field, (path.name, path.read_bytes(), mime)


def test_index_and_static(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "docpix" in r.text
    js = client.get("/static/js/app.js")
    assert js.status_code == 200 and len(js.text) > 1000
    css = client.get("/static/css/style.css")
    assert css.status_code == 200 and len(css.text) > 1000


def test_system_info(client):
    r = client.get("/api/system")
    assert r.status_code == 200
    data = r.json()
    assert data["license"] == "MIT"
    assert "Ghostscript" in data["license_note"]
    keys = {e["key"] for e in data["engines"]}
    assert {"libreoffice", "pandoc", "qpdf", "tesseract"} <= keys
    assert data["limits"]["max_batch_files"] > 0


def test_system_formats(client):
    r = client.get("/api/system/formats")
    assert r.status_code == 200
    data = r.json()
    assert "pdf" in data["convert_matrix"]["md"]
    assert "png" in data["image_output"]
    assert "chi_sim" in data["ocr_languages"]
    assert "merge" in data["pdf_ops"]


def test_health(client):
    r = client.get("/api/system/health")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_inspect_image(client, sample_jpg: Path):
    r = client.post("/api/inspect", files=[upload(sample_jpg, "file")])
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["kind"] == "image"
    assert data["image"]["width"] == 320


def test_inspect_pdf(client, sample_pdf: Path):
    r = client.post("/api/inspect", files=[upload(sample_pdf, "file")])
    assert r.status_code == 200
    assert r.json()["pdf"]["pages"] == 3


def test_inspect_empty_file_rejected(client):
    r = client.post("/api/inspect", files=[("file", ("empty.png", b"", "image/png"))])
    assert r.status_code == 400


def test_image_convert_job_end_to_end(client, sample_jpg: Path):
    r = client.post(
        "/api/jobs",
        data={"kind": "image", "op": "convert", "params": json.dumps({"format": "png", "quality": 70})},
        files=[upload(sample_jpg)],
    )
    assert r.status_code == 202, r.text
    job = r.json()
    assert job["status"] in ("pending", "running")

    done = wait_done(client, job["id"])
    assert done["status"] == "done", done
    assert done["progress"] == 1.0
    assert len(done["outputs"]) == 1
    out = done["outputs"][0]
    assert out["name"].endswith(".png") and out["kind"] == "image"

    dl = client.get(out["url"])
    assert dl.status_code == 200 and dl.content[:8] == b"\x89PNG\r\n\x1a\n"

    pv = client.get(out["preview_url"])
    assert pv.status_code == 200 and pv.headers["content-type"].startswith("image/")

    assert client.delete(f"/api/jobs/{job['id']}").status_code == 200
    assert client.get(f"/api/jobs/{job['id']}").status_code == 404


def test_pdf_merge_job_and_preview(client, sample_pdf: Path):
    r = client.post(
        "/api/jobs",
        data={"kind": "pdf", "op": "merge", "params": "{}"},
        files=[upload(sample_pdf), upload(sample_pdf)],
    )
    assert r.status_code == 202, r.text
    done = wait_done(client, r.json()["id"])
    assert done["status"] == "done", done
    out = done["outputs"][0]
    assert out["kind"] == "pdf"
    dl = client.get(out["url"])
    assert dl.status_code == 200 and dl.content[:5] == b"%PDF-"
    pv = client.get(out["preview_url"])
    assert pv.status_code == 200 and pv.content[:8] == b"\x89PNG\r\n\x1a\n"
    client.delete(f"/api/jobs/{done['id']}")


def test_pdf_extract_text_job(client, sample_pdf: Path):
    r = client.post("/api/jobs", data={"kind": "pdf", "op": "extract_text", "params": "{}"},
                    files=[upload(sample_pdf)])
    done = wait_done(client, r.json()["id"])
    assert done["status"] == "done", done
    out = done["outputs"][0]
    assert out["kind"] == "text"
    text = client.get(out["url"]).text
    assert "docpix" in text.lower()


def test_job_validation_errors(client, sample_jpg: Path):
    # 未知 kind
    assert client.post("/api/jobs", data={"kind": "nope", "op": "convert"},
                       files=[upload(sample_jpg)]).status_code == 400
    # kind 与 op 不匹配
    assert client.post("/api/jobs", data={"kind": "pdf", "op": "resize"},
                       files=[upload(sample_jpg)]).status_code == 400
    # convert 缺少 target
    assert client.post("/api/jobs", data={"kind": "convert", "op": "convert", "params": "{}"},
                       files=[upload(sample_jpg)]).status_code == 422
    # params 不是合法 JSON
    assert client.post("/api/jobs", data={"kind": "image", "op": "convert", "params": "{"},
                       files=[upload(sample_jpg)]).status_code == 400
    # quality 越界
    r = client.post("/api/jobs",
                    data={"kind": "image", "op": "convert", "params": json.dumps({"format": "png", "quality": 999})},
                    files=[upload(sample_jpg)])
    assert r.status_code == 422
    # 没有文件
    assert client.post("/api/jobs", data={"kind": "image", "op": "convert"}).status_code == 422


def test_job_not_found_and_list(client):
    assert client.get("/api/jobs/does-not-exist").status_code == 404
    assert client.post("/api/jobs/does-not-exist/cancel").status_code == 404
    assert client.delete("/api/jobs/does-not-exist").status_code == 404
    r = client.get("/api/jobs?limit=5")
    assert r.status_code == 200 and isinstance(r.json(), list)


def test_password_is_masked_in_record(client, sample_pdf: Path):
    r = client.post("/api/jobs",
                    data={"kind": "pdf", "op": "encrypt",
                          "params": json.dumps({"user_password": "topsecret", "allow_print": True})},
                    files=[upload(sample_pdf)])
    assert r.status_code == 202, r.text
    job = r.json()
    assert job["params"]["user_password"] == "***"
    done = wait_done(client, job["id"])
    assert done["status"] == "done", done
    client.delete(f"/api/jobs/{job['id']}")
