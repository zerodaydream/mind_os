_embedder: object | None = None
_embedder_model: str | None = None


def _quiet_onnx_runtime() -> None:
    try:
        import onnxruntime as ort

        ort.set_default_logger_severity(3)  # errors only; ORT CoreML fallback is noisy
    except ImportError:
        pass


def _onnx_providers(model_id: str) -> list[str]:
    try:
        import onnxruntime as ort
    except ImportError:
        return ["CPUExecutionProvider"]
    # shortcut: BGE ONNX is fast on CPU; CoreML compile often slower on laptop for these models
    if "bge" in model_id.lower():
        return ["CPUExecutionProvider"]
    avail = set(ort.get_available_providers())
    if "CoreMLExecutionProvider" in avail:
        return ["CoreMLExecutionProvider", "CPUExecutionProvider"]
    return ["CPUExecutionProvider"]


def _embed_input(model_id: str, text: str, *, is_query: bool) -> str:
    mid = model_id.lower()
    if "bge" in mid and is_query:
        return f"Represent this sentence for searching relevant passages: {text}"
    if "nomic" in mid:
        return f"{'search_query' if is_query else 'search_document'}: {text}"
    return text


def _get_embedder(model_id: str):
    global _embedder, _embedder_model  # noqa: PLW0603
    if _embedder is not None and _embedder_model == model_id:
        return _embedder
    try:
        from fastembed import TextEmbedding
    except ImportError as e:
        raise RuntimeError("fastembed missing: run uv sync") from e
    _quiet_onnx_runtime()
    inst = TextEmbedding(model_name=model_id, providers=_onnx_providers(model_id))
    _embedder = inst
    _embedder_model = model_id
    return inst


def embed_token_count(text: str, *, model_id: str, is_query: bool = False) -> int:
    """Tokenizer-aligned length for the active embedder (chunk sizing)."""
    if not text:
        return 0
    model = _get_embedder(model_id)
    return int(model.token_count(_embed_input(model_id, text, is_query=is_query)))


def embed_texts(texts: list[str], model_id: str, *, is_query: bool = False) -> list[list[float]]:
    """Local ONNX embeddings via fastembed (no torch / API quota)."""
    model = _get_embedder(model_id)
    batch = [_embed_input(model_id, t, is_query=is_query) for t in texts]
    return [vec.tolist() for vec in model.embed(batch)]