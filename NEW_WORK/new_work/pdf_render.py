from __future__ import annotations

import os
import sys
from pathlib import Path

from PIL import Image


DEFAULT_DPI = 100
JPEG_QUALITY = 85
MAX_PDF_PAGES = 100


def default_job_directory() -> Path:
    """Return a durable per-user location for prepared intake jobs."""
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return base / "NEW_WORK" / "jobs"


def render_pdf_to_jpegs(
    source: Path,
    output_directory: Path,
    *,
    dpi: int = DEFAULT_DPI,
    quality: int = JPEG_QUALITY,
) -> tuple[Path, ...]:
    """Render each PDF page into a numbered JPG without changing the source."""
    try:
        import pymupdf as fitz
    except ImportError as exc:
        raise RuntimeError("PDF 변환 모듈 PyMuPDF가 설치되지 않았습니다.") from exc

    source = Path(source).expanduser().resolve(strict=True)
    output_directory = Path(output_directory)
    if not source.is_file() or source.suffix.casefold() != ".pdf":
        raise ValueError("변환할 PDF 파일을 선택하세요.")
    if not 72 <= dpi <= 600 or not 60 <= quality <= 100:
        raise ValueError("PDF 변환 해상도 또는 JPG 품질 값이 범위를 벗어났습니다.")

    document = fitz.open(source)
    pdfium_document = None
    outputs: list[Path] = []
    try:
        if document.needs_pass:
            raise ValueError(f"암호가 설정된 PDF는 변환할 수 없습니다: {source.name}")
        if not 1 <= len(document) <= MAX_PDF_PAGES:
            raise ValueError(
                f"PDF 페이지 수는 1~{MAX_PDF_PAGES}장이어야 합니다: {source.name}"
            )
        output_directory.mkdir(parents=True, exist_ok=False)
        scale = dpi / 72.0
        for index, page in enumerate(document):
            pixmap = page.get_pixmap(
                matrix=fitz.Matrix(scale, scale),
                colorspace=fitz.csRGB,
                alpha=False,
            )
            image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
            if pixmap.is_unicolor:
                if pdfium_document is None:
                    try:
                        import pypdfium2 as pdfium
                    except ImportError as exc:
                        raise RuntimeError(
                            "PDFium이 없어 빈 페이지를 재확인할 수 없습니다. "
                            "pypdfium2를 설치하세요."
                        ) from exc
                    pdfium_document = pdfium.PdfDocument(str(source))
                image = pdfium_document[index].render(scale=scale).to_pil().convert("RGB")
                if all(low == high for low, high in image.getextrema()):
                    raise ValueError(
                        f"PDF {index + 1}페이지가 빈 이미지로 변환되어 중단했습니다."
                    )

            target = output_directory / f"{index + 1:03d}.jpg"
            temporary = output_directory / f".{index + 1:03d}.jpg.partial"
            try:
                image.save(temporary, format="JPEG", quality=quality, optimize=False)
                if temporary.stat().st_size == 0:
                    raise ValueError(f"PDF {index + 1}페이지 이미지가 비었습니다.")
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
            outputs.append(target)
        return tuple(outputs)
    finally:
        if pdfium_document is not None:
            pdfium_document.close()
        document.close()
