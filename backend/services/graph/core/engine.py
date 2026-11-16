"""LangGraph optional dependency probe."""
def langgraph_available() -> bool:
    """Return True when langgraph is importable."""
    try:
        import langgraph  # noqa: F401
    except ImportError:
        return False
    return True
