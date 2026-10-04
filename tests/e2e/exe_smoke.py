"""对**运行中的** docpix 服务跑真实任务，验证各引擎链路是否可用。

两种用法：

1. 打已启动的服务（开发时最常见）：

       .\\.venv\\Scripts\\python.exe tests\\e2e\\exe_smoke.py --base-url http://127.0.0.1:8765

2. 打打包产物：把 ``dist\\docpix`` 复制到一个干净目录再启动（模拟用户解压即用）：

       .\\.venv\\Scripts\\python.exe tests\\e2e\\exe_smoke.py --launch dist\\docpix --engines

   ``--engines`` 会把仓库里的 ``bin/`` 目录联接（junction）进那个干净目录，
   这样连「转换 / OCR」这类需要外部引擎的链路也能一起验证；不加则只验证
   免引擎的部分（图片处理、PDF 处理），引擎相关用例会如实报 FAIL。

覆盖图片互转、to_ico、HEIC 解码、多图拼接、md→pdf（pandoc+LibreOffice）、
PDF 预览（pdfium）、PDF 合并（pikepdf）、PDF 转图片、OCR（ocrmypdf 子进程重入）
以及 OCR 产物的可检索文本层。
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
FIXTURES = HERE / "fixtures"

results: list[tuple[bool, str, str]] = []
BASE = ""


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((bool(ok), name, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  →  {detail}" if detail else ""))


# ------------------------------------------------------------------ HTTP 工具
def submit(kind: str, op: str, params: dict, files: list[Path]):
    boundary = "----docpixsmoke"
    body = b""
    for k, v in (("kind", kind), ("op", op), ("params", json.dumps(params, ensure_ascii=False))):
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n").encode("utf-8")
    for p in files:
        body += (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; "
            f"filename=\"{p.name}\"\r\nContent-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8") + p.read_bytes() + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"{BASE}/api/jobs", data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8", "replace"))


def run_job(label: str, kind: str, op: str, params: dict, files: list[Path], timeout: float = 300.0):
    t0 = time.time()
    code, job = submit(kind, op, params, files)
    if code != 202:
        check(f"{label} · 提交成功", False, f"{code} {str(job)[:200]}")
        return None
    jid = job["id"]
    rec = job
    while time.time() - t0 < timeout:
        with urllib.request.urlopen(f"{BASE}/api/jobs/{jid}", timeout=10) as r:
            rec = json.load(r)
        if rec["status"] in ("done", "error", "cancelled"):
            break
        time.sleep(0.3)
    ok = rec["status"] == "done"
    check(f"{label} · 任务完成", ok,
          f"{rec['status']} {time.time() - t0:.1f}s" + ("" if ok else f" | {str(rec.get('error'))[:200]}"))
    return rec if ok else None


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(BASE + url, timeout=120) as r:
        return r.read()


def head(data: bytes, n: int = 4) -> str:
    return ",".join(str(b) for b in data[:n])


def _text_layer(data: bytes) -> tuple[bool, str]:
    """PDF 里是否有可检索文本层（命中 OCR 样张里的关键词）。"""
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        text = "\n".join((p.extract_text() or "") for p in reader.pages).upper()
        hits = [w for w in ("HELLO", "DOCPIX", "1234567890") if w in text]
        return len(hits) >= 2, f"命中 {hits}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


# ------------------------------------------------------------------ 打包产物启动
def free_port(preferred: int) -> int:
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", preferred)) != 0:
            return preferred
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def launch_packaged(src: Path, workdir: Path, port: int, engines: bool = False) -> subprocess.Popen:
    """把打包目录复制到干净位置并启动，返回进程对象。"""
    if not (src / "docpix.exe").is_file():
        raise SystemExit(f"{src} 里没有 docpix.exe（先跑 tools\\build_exe.ps1）")
    if workdir.exists():
        shutil.rmtree(workdir, ignore_errors=True)
    shutil.copytree(src, workdir)

    if engines:
        # 用目录联接把仓库的 bin/ 挂进来（不需要管理员权限，也不占额外空间）
        target = workdir / "bin"
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        rc = subprocess.run(["cmd", "/c", "mklink", "/J", str(target), str(ROOT / "bin")],
                            capture_output=True, text=True)
        if rc.returncode != 0:
            raise SystemExit(f"创建 bin 目录联接失败：{rc.stdout} {rc.stderr}")
        print(f"  已联接引擎目录：{target} -> {ROOT / 'bin'}")

    env = dict(os.environ)
    env["DOCPIX_PORT"] = str(port)
    env["DOCPIX_NO_BROWSER"] = "1"
    out = (workdir / "smoke.log").open("wb")
    proc = subprocess.Popen(
        [str(workdir / "docpix.exe")], cwd=str(workdir), env=env,
        stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
        creationflags=(subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                       | subprocess.CREATE_NO_WINDOW),
    )
    print(f"  已启动打包产物 pid={proc.pid}  工作目录={workdir}")
    return proc


def wait_ready(timeout: float = 120.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(f"{BASE}/api/system", timeout=3) as r:
                if r.status == 200:
                    print(f"  服务就绪（{time.time() - t0:.1f}s）")
                    return True
        except (urllib.error.URLError, OSError, TimeoutError):
            time.sleep(0.5)
    return False


# ---------------------------------------------------------------------- 用例
def main() -> int:
    global BASE
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="docpix 端到端冒烟测试")
    parser.add_argument("--base-url", default="http://127.0.0.1:8766")
    parser.add_argument("--fixtures", type=Path, default=FIXTURES)
    parser.add_argument("--launch", type=Path, help="打包目录（含 docpix.exe），复制到干净目录后启动再测")
    parser.add_argument("--engines", action="store_true", help="配合 --launch：把仓库 bin/ 联接进部署目录")
    parser.add_argument("--workdir", type=Path, default=ROOT / ".tmp" / "e2e-packaged")
    parser.add_argument("--timeout", type=float, default=300.0, help="单个任务超时（秒）")
    args = parser.parse_args()

    if not args.fixtures.is_dir() or not (args.fixtures / "img_a.png").is_file():
        print("素材缺失，正在生成…")
        subprocess.run([sys.executable, str(HERE / "make_fixtures.py"), "--out", str(args.fixtures)],
                       check=True)

    fx = args.fixtures
    port = int(args.base_url.rsplit(":", 1)[-1].rstrip("/"))
    proc = None
    if args.launch:
        port = free_port(port)
        args.base_url = f"http://127.0.0.1:{port}"
        proc = launch_packaged(args.launch.resolve(), args.workdir.resolve(), port, args.engines)
    BASE = args.base_url.rstrip("/")

    try:
        if not wait_ready():
            check("服务就绪", False, BASE)
            log = args.workdir / "smoke.log"
            if log.is_file():
                print("--- smoke.log ---")
                print(log.read_text(encoding="utf-8", errors="replace")[-1500:])
            return 1

        # 1. 引擎与健康
        with urllib.request.urlopen(f"{BASE}/api/system", timeout=10) as r:
            info = json.load(r)
        check("版本与许可", info.get("version") == "1.0.0" and info.get("license") == "MIT",
              f"{info.get('version')} / {info.get('license')}")
        engines = {e["key"]: e["available"] for e in info["engines"]}
        check("四个引擎都被识别", all(engines.values()), json.dumps(engines))
        with urllib.request.urlopen(f"{BASE}/api/system/health", timeout=10) as r:
            health = json.load(r)
        check("必需引擎齐全", not health["missing_required"], str(health["missing_required"]))

        # 2. 图片链路
        rec = run_job("图片 PNG→WebP", "image", "convert", {"format": "webp", "quality": 82},
                      [fx / "img_a.png"], args.timeout)
        if rec:
            data = fetch(rec["outputs"][0]["url"])
            check("WebP 产物是 WebP", len(data) > 100 and data[8:12] == b"WEBP",
                  f"{rec['outputs'][0]['name']} {len(data)}B")

        rec = run_job("图片 生成 .ico", "image", "to_ico", {"size": 128}, [fx / "img_a.png"], args.timeout)
        if rec:
            out = rec["outputs"][0]
            data = fetch(out["url"])
            check("to_ico 产物扩展名 .ico", out["name"].endswith(".ico"), out["name"])
            check("to_ico 是 ICO 魔数", head(data) == "0,0,1,0", head(data))

        if (fx / "sample.heic").is_file():
            rec = run_job("图片 HEIC→PNG（libheif）", "image", "convert", {"format": "png"},
                          [fx / "sample.heic"], args.timeout)
            if rec:
                data = fetch(rec["outputs"][0]["url"])
                check("HEIC 解码成功且输出 PNG", head(data) == "137,80,78,71", f"{len(data)}B")

        rec = run_job("图片 三图拼接", "image", "combine", {"direction": "vertical", "gap": 8},
                      [fx / "img_a.png", fx / "img_b.png", fx / "img_c.png"], args.timeout)
        if rec:
            data = fetch(rec["outputs"][0]["url"])
            check("拼接产物是 PNG", head(data) == "137,80,78,71", f"{len(data)}B")

        # 3. 转换链路（pandoc + LibreOffice 两跳）
        conv = run_job("转换 md→pdf（pandoc+office）", "convert", "convert", {"target": "pdf"},
                       [fx / "notes.md"], max(args.timeout, 420))
        if conv:
            data = fetch(conv["outputs"][0]["url"])
            check("md→pdf 产物是 PDF", data[:4] == b"%PDF", f"{len(data)}B")
            plan = conv.get("plan") or []
            ops = [st.get("op") for pl in plan for st in (pl.get("steps") or [])]
            check("记录里有两跳链路 pandoc→office", ops == ["pandoc", "office"], str(ops))
            pname = conv["outputs"][0]["name"]
            with urllib.request.urlopen(f"{BASE}/api/jobs/{conv['id']}/preview/{pname}", timeout=120) as r:
                pv = r.read()
            check("PDF 预览出图（pdfium）", head(pv) == "137,80,78,71", f"{len(pv)}B")

        # 4. PDF 链路
        rec = run_job("PDF 合并两文件", "pdf", "merge", {},
                      [fx / "report.pdf", fx / "report.pdf"], args.timeout)
        if rec:
            data = fetch(rec["outputs"][0]["url"])
            check("合并产物是 PDF", data[:4] == b"%PDF", f"{len(data)}B")

        rec = run_job("PDF 转图片", "pdf", "pdf_to_images", {"format": "png", "dpi": 96},
                      [fx / "report.pdf"], args.timeout)
        if rec:
            check("PDF 转图片至少 1 张", len(rec["outputs"]) >= 1, f"{len(rec['outputs'])} 张")
            data = fetch(rec["outputs"][0]["url"])
            check("转出的图片是 PNG", head(data) == "137,80,78,71", f"{len(data)}B")

        # 5. OCR。两条链路都要覆盖：
        #    图片 → tesseract 直接产 PDF；PDF → ocrmypdf（冻结后靠 -m 重入，最易坏）
        ocr = run_job("OCR 图片→可搜索 PDF", "ocr", "ocr",
                      {"languages": ["chi_sim"], "output_type": "pdf", "optimize": 1},
                      [fx / "ocr_sample.png"], max(args.timeout, 420))
        if ocr:
            data = fetch(ocr["outputs"][0]["url"])
            check("OCR 产物是 PDF", data[:4] == b"%PDF", f"{len(data)}B")
            check("OCR 产物含可检索文本层", *_text_layer(data))

        if (fx / "scan.pdf").is_file():
            t0 = time.time()
            ocr2 = run_job("OCR 无文字层 PDF→可搜索 PDF（ocrmypdf）", "ocr", "ocr",
                           {"languages": ["chi_sim"], "output_type": "pdf", "optimize": 1},
                           [fx / "scan.pdf"], max(args.timeout, 600))
            if ocr2:
                data = fetch(ocr2["outputs"][0]["url"])
                check("ocrmypdf 产物是 PDF", data[:4] == b"%PDF",
                      f"{len(data)}B，用时 {time.time() - t0:.1f}s")
                check("ocrmypdf 给无文字层 PDF 加上了文本层", *_text_layer(data))

        # 6. 任务列表
        with urllib.request.urlopen(f"{BASE}/api/jobs?limit=100", timeout=20) as r:
            jobs = json.load(r)
        check("任务列表可读", r.status == 200 and len(jobs) >= 8, f"{len(jobs)} 条")
    finally:
        if proc is not None:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, check=False)

    total = len(results)
    passed = sum(1 for ok, _, _ in results if ok)
    print(f"\n{'=' * 60}\n通过 {passed}/{total}")
    if passed != total:
        print("失败项：")
        for ok, name, detail in results:
            if not ok:
                print(f"  - {name}  →  {detail}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
