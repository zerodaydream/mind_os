_CHUNK_SIZE = 1500


def chunk_text(text: str, *, version: str = "v1") -> list[tuple[int, str]]:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return [(0, text[:_CHUNK_SIZE])]
    chunks: list[tuple[int, str]] = []
    buf = ""
    ord_ = 0
    for para in paragraphs:
        if len(buf) + len(para) + 2 > _CHUNK_SIZE and buf:
            chunks.append((ord_, buf.strip()))
            ord_ += 1
            buf = ""
        buf = f"{buf}\n\n{para}".strip() if buf else para
    if buf:
        chunks.append((ord_, buf.strip()))
    return chunks or [(0, text[:_CHUNK_SIZE])]
