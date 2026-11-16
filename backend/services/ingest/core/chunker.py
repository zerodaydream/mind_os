import re

from backend.common.config_loader.settings import get_settings

_CHUNK_TOKEN_BUDGET = 508  # bge truncates at 512; leave headroom for tokenizer specials
_CHUNK_OVERLAP_TOKENS = 40
_ATTACHMENT_HEAD = "### Attachment:"


def _tok(text: str, model_id: str) -> int:
    from backend.services.retrieval.core.embed import embed_token_count

    return embed_token_count(text, model_id=model_id, is_query=False)


def _fit_end(text: str, *, start: int, model_id: str) -> int:
    if start >= len(text):
        return start
    if _tok(text[start:], model_id) <= _CHUNK_TOKEN_BUDGET:
        return len(text)
    lo, hi = start + 1, len(text)
    best = start + 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if _tok(text[start:mid], model_id) <= _CHUNK_TOKEN_BUDGET:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def _overlap_tail(chunk: str, model_id: str) -> str:
    if _tok(chunk, model_id) <= _CHUNK_OVERLAP_TOKENS:
        return chunk
    for end in range(len(chunk), 0, -1):
        for start in range(max(0, end - 800), end):
            tail = chunk[start:end]
            if _tok(tail, model_id) <= _CHUNK_OVERLAP_TOKENS:
                return tail
    return chunk


def _split_by_token_budget(text: str, model_id: str) -> list[str]:
    if _tok(text, model_id) <= _CHUNK_TOKEN_BUDGET:
        return [text]
    parts: list[str] = []
    start = 0
    while start < len(text):
        end = _fit_end(text, start=start, model_id=model_id)
        parts.append(text[start:end])
        if end >= len(text):
            break
        start = max(start + 1, end - 400)
        while start < end and _tok(text[start:end], model_id) > _CHUNK_OVERLAP_TOKENS:
            start += 1
    return parts


def _chunk_paragraphs(text: str, *, model_id: str, start_ord: int = 0) -> list[tuple[int, str]]:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        head = _split_by_token_budget(text, model_id)
        return [(start_ord, head[0])] if head else [(start_ord, "")]
    chunks: list[tuple[int, str]] = []
    buf = ""
    ord_ = start_ord
    for para in paragraphs:
        if _tok(para, model_id) > _CHUNK_TOKEN_BUDGET:
            if buf:
                chunks.append((ord_, buf.strip()))
                ord_ += 1
                buf = ""
            for piece in _split_by_token_budget(para, model_id):
                chunks.append((ord_, piece))
                ord_ += 1
            continue
        merged = f"{buf}\n\n{para}".strip() if buf else para
        if _tok(merged, model_id) > _CHUNK_TOKEN_BUDGET and buf:
            chunk = buf.strip()
            chunks.append((ord_, chunk))
            ord_ += 1
            buf = f"{_overlap_tail(chunk, model_id)}\n\n{para}".strip()
        else:
            buf = merged
    if buf:
        chunks.append((ord_, buf.strip()))
    return chunks or [(start_ord, _split_by_token_budget(text, model_id)[0])]


def _chunk_v2(text: str, model_id: str) -> list[tuple[int, str]]:
    parts = re.split(r"(?=\n" + re.escape(_ATTACHMENT_HEAD) + ")", text.strip())
    if len(parts) <= 1:
        return _chunk_paragraphs(text, model_id=model_id)
    out: list[tuple[int, str]] = []
    ord_ = 0
    for part in parts:
        block = part.strip()
        if not block:
            continue
        if _tok(block, model_id) <= _CHUNK_TOKEN_BUDGET:
            out.append((ord_, block))
            ord_ += 1
        else:
            for o, c in _chunk_paragraphs(block, model_id=model_id, start_ord=ord_):
                out.append((o, c))
                ord_ = o + 1
    if not out:
        head = _split_by_token_budget(text, model_id)
        return [(0, head[0])] if head else [(0, "")]
    return out


def chunk_text(
    text: str,
    *,
    version: str = "v1",
    embedding_model: str | None = None,
) -> list[tuple[int, str]]:
    model_id = embedding_model or get_settings().embedding_model
    ver = (version or "v1").strip().lower()
    if ver == "v2":
        return _chunk_v2(text, model_id)
    return _chunk_paragraphs(text, model_id=model_id)