import math

_google_client: object | None = None


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector))
    if norm <= 0:
        return vector
    return [x / norm for x in vector]


def embed_texts(texts: list[str], model_id: str, *, is_query: bool = False) -> list[list[float]]:
    """Embed texts with the Google Generative AI embedding API."""
    from google import genai
    from google.genai import types

    from backend.common.config_loader.settings import get_settings
    from backend.common.ssl_env import fix_ssl_cert_env

    settings = get_settings()
    api_key = settings.google_api_key
    if not api_key.strip():
        raise RuntimeError("GOOGLE_API_KEY is required for embeddings")

    global _google_client
    fix_ssl_cert_env()
    if _google_client is None:
        _google_client = genai.Client(api_key=api_key)
    client = _google_client
    task_type = "RETRIEVAL_QUERY" if is_query else "RETRIEVAL_DOCUMENT"
    config = types.EmbedContentConfig(
        task_type=task_type,
        output_dimensionality=settings.embedding_dimensions,
    )
    out: list[list[float]] = []
    for start in range(0, len(texts), 100):
        batch = texts[start : start + 100]
        response = client.models.embed_content(model=model_id, contents=batch, config=config)
        for emb in response.embeddings or []:
            values = list(emb.values or [])
            dim = settings.embedding_dimensions
            if len(values) != dim:
                values = values[:dim]
            out.append(_normalize(values))
    return out