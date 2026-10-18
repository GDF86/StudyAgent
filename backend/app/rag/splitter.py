from app.core.errors import DomainError
from app.rag.loaders import TextPage


def split_pages(pages: list[TextPage], size: int, overlap: int, max_chunks: int) -> list[TextPage]:
    """Split in character windows, keeping overlap and never crossing PDF pages."""
    if size < 1 or not 0 <= overlap < size:
        raise ValueError("invalid chunk size/overlap")
    chunks = []
    for page in pages:
        for start in range(0, len(page.content), size - overlap):
            content = page.content[start:start + size]
            if content.strip():
                chunks.append(TextPage(content, page.page))
            if len(chunks) > max_chunks:
                raise DomainError(413, "TOO_MANY_CHUNKS", "文本分片数量超过限制，请拆分文档")
            if start + size >= len(page.content):
                break
    return chunks
