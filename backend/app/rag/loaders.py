from dataclasses import dataclass
from pathlib import Path
import re
from zipfile import ZipFile

import pymupdf
from docx import Document as DocxDocument
from docx.table import Table

from app.core.errors import DomainError


@dataclass(frozen=True)
class TextPage:
    content: str
    page: int | None


def clean_text(text: str) -> str:
    """Normalize line endings and controls while retaining paragraphs and Markdown."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


def parse_document(path: Path, suffix: str, max_chars: int) -> list[TextPage]:
    """Extract text with real PDF page numbers; other formats have no stable pages."""
    pages: list[TextPage] = []
    total = 0

    def add(text: str, page: int | None = None):
        nonlocal total
        text = clean_text(text)
        total += len(text)
        if total > max_chars:
            raise DomainError(413, "TEXT_TOO_LARGE", "解析文本超过限制，请拆分文档")
        if text:
            pages.append(TextPage(text, page))

    try:
        if suffix == ".pdf":
            with pymupdf.open(stream=path.read_bytes(), filetype="pdf") as pdf:
                if not pdf.is_pdf or pdf.needs_pass:
                    raise ValueError("invalid or encrypted PDF")
                for number, page in enumerate(pdf, 1):
                    add(page.get_text("text", sort=True), number)
        elif suffix == ".docx":
            with ZipFile(path) as archive:
                if sum(entry.file_size for entry in archive.infolist()) > 50 * 1024 * 1024:
                    raise DomainError(413, "DOCUMENT_TOO_LARGE", "DOCX 解压后超过限制，请拆分文档")
            doc = DocxDocument(path)
            parts = []
            docx_chars = 0
            for block in doc.iter_inner_content():
                text = "\n".join("\t".join(cell.text for cell in row.cells) for row in block.rows) if isinstance(block, Table) else block.text
                parts.append(text)
                docx_chars += len(text)
                if docx_chars > max_chars:
                    raise DomainError(413, "TEXT_TOO_LARGE", "解析文本超过限制，请拆分文档")
            add("\n".join(parts))
        else:
            data = path.read_bytes()
            encoding = "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
            try:
                text = data.decode(encoding)
            except UnicodeDecodeError:
                text = data.decode("gb18030")
            if "\x00" in text:
                raise ValueError("binary data")
            add(text)
    except DomainError:
        raise
    except Exception:
        raise DomainError(422, "DOCUMENT_PARSE_FAILED", "文档解析失败：请检查格式、编码或加密状态") from None
    if not pages:
        raise DomainError(422, "DOCUMENT_EMPTY_TEXT", "未提取到文本；扫描版 PDF 暂不支持 OCR")
    return pages
