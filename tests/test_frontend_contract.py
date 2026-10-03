"""前后端契约测试：前端表单定义必须覆盖后端能力表。

这类漂移已经出现过多次（`image_input` 带不带点、`ocr_languages` 的嵌套结构、
`to_ico` 没有入口、`optimize` 是 checkbox 而后端要 0-3 档），所以用测试把它们
钉死，避免以后给引擎加能力时前端忘了跟上。
"""

from __future__ import annotations

import re
from pathlib import Path

from app import convert
from app.engines import imageops

APP_JS = Path(__file__).resolve().parent.parent / "app" / "static" / "js" / "app.js"


def _js() -> str:
    return APP_JS.read_text(encoding="utf-8")


def _object_keys(source: str, const_name: str) -> list[str]:
    """粗略解析 ``const X = { a: {...}, b: {...} };`` 的顶层键。"""
    start = source.index(f"const {const_name} = {{")
    i = source.index("{", start)
    depth = 0
    end = len(source)
    for j in range(i, len(source)):
        if source[j] == "{":
            depth += 1
        elif source[j] == "}":
            depth -= 1
            if depth == 0:
                end = j
                break
    body = source[i + 1:end]
    keys: list[str] = []
    depth = 0
    for line in body.splitlines():
        stripped = line.strip()
        if depth == 0:
            match = re.match(r"^([A-Za-z_]\w*)\s*:\s*\{", stripped)
            if match:
                keys.append(match.group(1))
        depth += line.count("{") - line.count("}")
    return keys


def _check_ops(const_name: str, backend: list[str]) -> None:
    keys = _object_keys(_js(), const_name)
    assert keys, f"没能解析出 {const_name}"
    missing = sorted(set(backend) - set(keys))
    unknown = sorted(set(keys) - set(backend))
    assert not missing, f"后端有、前端没有入口的操作（{const_name}）：{missing}"
    assert not unknown, f"前端有、后端不认识的操作（{const_name}）：{unknown}"


def test_frontend_image_ops_cover_backend():
    _check_ops("IMAGE_OPS", convert.capability_summary()["image_ops"])


def test_frontend_pdf_ops_cover_backend():
    _check_ops("PDF_OPS", convert.capability_summary()["pdf_ops"])


def test_op_labels_cover_ops():
    """每个操作都要有中文名，否则下拉框显示成裸英文。"""
    source = _js()
    labels_block = source[source.index("const OP_LABELS = {"):]
    labels_block = labels_block[:labels_block.index("};")]
    ops = set(_object_keys(source, "IMAGE_OPS")) | set(_object_keys(source, "PDF_OPS"))
    missing = sorted(op for op in ops if f"{op}:" not in labels_block)
    assert not missing, f"OP_LABELS 缺少：{missing}"


def test_ocr_output_types_come_from_backend():
    """OCR 输出类型必须由 /api/system/formats 驱动，不能写死在表单里。"""
    source = _js()
    assert "source: 'ocr_output_types'" in source
    match = re.search(r"const OCR_OUTPUT_FALLBACK = \[([^\]]*)\]", source)
    assert match, "缺少 OCR_OUTPUT_FALLBACK 兜底"
    values = [v.strip().strip("'\"") for v in match.group(1).split(",") if v.strip()]
    assert values == convert.capability_summary()["ocr_output_types"]


def test_ocr_optimize_is_numeric_select():
    """optimize 是 ocrmypdf 的 0-3 档位，不能是复选框（以前发 false 被强转成 0）。"""
    source = _js()
    block = source[source.index("const OCR_FIELDS = ["):]
    block = block[:block.index("];")]
    assert "F.select('optimize'" in block
    assert "numeric: true" in block
    assert "F.check('optimize'" not in block


def test_image_input_exts_are_dotless_and_frontend_normalizes():
    """两边扩展名必须归一化后再比对（历史上这里是「带点 / 不带点」打架）。"""
    assert all(not ext.startswith(".") for ext in imageops.INPUT_FORMATS), \
        "后端约定：image_input 是不带点的裸扩展名"
    source = _js()
    assert "function normExt(" in source
    allowed = source[source.index("function allowedExts"):][:900]
    # 白名单是把后端扩展名 map(normExt) 归一化成「带点 + 小写」再比对
    assert ".map(normExt)" in allowed, "白名单必须用 normExt 归一化后再比对"
    assert "extWithDot(" in allowed or "extWithDot(" in source


def test_to_ico_batch_outputs_ico_file(tmp_path):
    """to_ico 必须产出 .ico，而不是「.png 文件名里装 ICO 字节」。"""
    from PIL import Image

    src = tmp_path / "in.png"
    Image.new("RGBA", (64, 64), (255, 0, 0, 255)).save(src)
    out = imageops.batch([src], tmp_path / "out", "to_ico", params={"size": 48})
    assert out[0].suffix == ".ico", f"产物扩展名错误：{out[0].name}"
    with Image.open(out[0]) as img:
        assert img.format == "ICO"
        assert max(img.size) <= 48
