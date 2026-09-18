from astrbot_plugin_direct_checkin.utils.text_utils import (
    normalize_text,
    normalize_text_lines,
    select_relevant_text,
    similarity,
    text_sha256,
)


def test_normalize_text_collapses_whitespace_and_width():
    assert normalize_text("Ａ  B\n\nC\tD") == "a b c d"
    assert normalize_text("a\u200bb") == "ab"


def test_normalize_text_lines_keeps_paragraphs():
    assert normalize_text_lines("第一段\n\n第二段  ") == "第一段\n第二段"


def test_text_sha256_ignores_formatting():
    assert text_sha256("Hello   World") == text_sha256("hello world")
    assert text_sha256("abc") != text_sha256("abd")


def test_similarity_identical_and_different():
    assert similarity("今天实现了文件上传接口并补了测试", "今天实现了文件上传接口并补了测试") == 1.0
    assert similarity("今天实现了文件上传接口", "今天打了一整天游戏看剧") < 0.2


def test_select_relevant_text_keeps_keyword_lines():
    text = "\n".join(["开头说明"] + ["废话内容" * 300 for _ in range(20)] + ["下一步：接入鉴权"])
    selected = select_relevant_text(text, max_chars=400)
    assert len(selected) <= 400
    assert "下一步" in selected
