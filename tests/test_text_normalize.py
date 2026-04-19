from app.text_normalize import normalize_document_text


def test_normalize_collapses_whitespace() -> None:
    s = "  hello  \n\n\n  world  \u200b  "
    out = normalize_document_text(s)
    assert "hello" in out
    assert "world" in out
    assert "\n\n\n" not in out


def test_normalize_removes_page_line() -> None:
    s = "经历A\n第 2 页\n经历B"
    out = normalize_document_text(s)
    assert "第 2 页" not in out
    assert "经历A" in out
    assert "经历B" in out
