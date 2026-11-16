def embed_texts(texts: list[str], model_id: str) -> list[list[float]]:
    """Embed texts using the model."""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as e:
        raise RuntimeError("Install embed extra: uv sync --extra embed") from e
    model = SentenceTransformer(model_id)
    return model.encode(texts, normalize_embeddings=True).tolist()
