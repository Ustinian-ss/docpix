"""系统信息与自检路由：``/api/system*``。"""

from __future__ import annotations

import platform
import shutil
import sys

from fastapi import APIRouter

from .. import __version__, config, convert
from ..engines import ocr, office, pandoc, registry
from ..jobs import manager
from ..models import EngineInfo, FormatsInfo, Limits, Retention, SystemInfo

router = APIRouter(prefix="/api/system", tags=["system"])

LICENSE_NOTE = (
    "docpix 自身以 MIT 许可发布。刻意避开 Ghostscript(AGPL-3.0)、"
    "PyMuPDF(AGPL-3.0)、Poppler(GPL-2.0)，以保证整体许可干净；"
    "Pandoc 是 GPL-2.0+，仅以独立进程方式调用且不随仓库分发。"
)


def _engines() -> list[dict]:
    items = registry.detect()
    for item in items:
        if item["key"] == "pandoc":
            item["version"] = pandoc.version()
        elif item["key"] == "libreoffice":
            item["hint"] = None if item["available"] else "运行 pwsh tools\\fetch_engines.ps1"
        elif item["key"] == "tesseract":
            status = ocr.status()
            item["version"] = status.get("tesseract_version")
            item["hint"] = status.get("hint")
        elif item["key"] == "qpdf":
            item["hint"] = None if item["available"] else "运行 pwsh tools\\fetch_engines.ps1 -Only qpdf"
    return items


@router.get("", response_model=SystemInfo)
def system_info() -> SystemInfo:
    missing = registry.missing_required()
    return SystemInfo(
        version=__version__,
        python=sys.version.split()[0],
        platform=f"{platform.system()} {platform.release()}",
        license="MIT",
        license_note=LICENSE_NOTE,
        engines=[EngineInfo(**e) for e in _engines()],
        limits=Limits(),
        retention=Retention(),
    )


@router.get("/engines", response_model=list[EngineInfo])
def engines() -> list[EngineInfo]:
    return [EngineInfo(**e) for e in _engines()]


@router.get("/formats", response_model=FormatsInfo)
def formats() -> FormatsInfo:
    from ..engines import imageops

    caps = convert.capability_summary()
    return FormatsInfo(
        image_input=list(imageops.INPUT_FORMATS),
        image_output=list(imageops.OUTPUT_FORMATS),
        office_targets=caps["office_targets"],
        pandoc_targets=caps["pandoc_targets"],
        convert_matrix=caps["matrix"],
        image_ops=caps["image_ops"],
        pdf_ops=caps["pdf_ops"],
        ocr_languages=ocr.languages(),
        installed_ocr_languages=ocr.installed_languages(),
        ocr_output_types=caps["ocr_output_types"],
    )


@router.get("/health")
def health() -> dict:
    usage = shutil.disk_usage(str(config.PROJECT_ROOT))
    return {
        "ok": True,
        "jobs": manager.stats(),
        "missing_required": registry.missing_required(),
        "libreoffice": office.available(),
        "pandoc": pandoc.available(),
        "ocr": ocr.available(),
        "disk": {
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
        },
    }


__all__ = ["router"]
