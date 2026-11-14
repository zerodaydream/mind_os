from backend.services.agents.core.markdown_tables import normalize_markdown_tables


def test_normalizes_and_drops_placeholders():
    raw = """Here:

| Subject | Description |
| :--- | :--- |
| *[Subject 1]* | *[Job description snippet]* |
| Indeed Application: AI Developer | Applied via Indeed |
"""
    out = normalize_markdown_tables(raw)
    assert "| --- | --- |" in out
    assert "Indeed Application: AI Developer" in out
    assert "*[Subject 1]*" not in out