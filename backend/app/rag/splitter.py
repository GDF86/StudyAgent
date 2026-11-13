from app.core.errors import DomainError
from app.rag.loaders import TextPage


def split_pages(pages: list[TextPage], size: int, overlap: int, max_chunks: int, *, paragraph_aware=False, max_utf8_bytes=None) -> list[TextPage]:
    """Split in character windows, keeping overlap and never crossing PDF pages."""
    if size < 1 or not 0 <= overlap < size or (max_utf8_bytes is not None and max_utf8_bytes<4):
        raise ValueError("invalid chunk size/overlap")
    chunks = [];count=0
    for page in pages:
        start=0
        while start<len(page.content):
            end=min(start+size,len(page.content))
            if max_utf8_bytes:
                while len(page.content[start:end].encode('utf-8'))>max_utf8_bytes:end-=1
                if end<=start:raise ValueError('byte limit cannot fit one character')
            if paragraph_aware and end<len(page.content):
                cut=page.content.rfind('\n',start+max(size//2,overlap+1),end)
                if cut!=-1:end=cut+1
            content = page.content[start:end]
            if content.strip():
                count+=1
                if count<=max_chunks:chunks.append(TextPage(content,page.page))
            if end>=len(page.content):break
            start=max(start+1,end-min(overlap,max(0,(end-start)//4))) if max_utf8_bytes else max(start+1,end-overlap)
    if count>max_chunks:
        raise DomainError(413,'TOO_MANY_CHUNKS',f'已解析{sum(len(p.content) for p in pages)}字符，实际需要{count}个分片，超过上限{max_chunks}；请精简重复内容或按章节分卷。Embedding未调用。',{'parsed_chars':sum(len(p.content) for p in pages),'planned_chunks':count,'embedding_called':False})
    return chunks
