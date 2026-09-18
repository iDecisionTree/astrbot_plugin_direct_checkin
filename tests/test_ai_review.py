import pytest
from astrbot_plugin_direct_checkin.services.ai_review_service import (
    normalize_ai_data,
    parse_ai_response,
)


def test_parse_plain_json():
    data = parse_ai_response('{"decision": "pass", "brief_feedback": "有进展"}')
    assert data["decision"] == "pass"


def test_parse_json_inside_code_fence():
    raw = '```json\n{"decision": "fail", "brief_feedback": "内容太少"}\n```'
    data = parse_ai_response(raw)
    assert data["decision"] == "fail"


def test_parse_json_with_surrounding_text():
    raw = '这是结果：{"decision":"pass","confidence":0.8} 请查收'
    data = parse_ai_response(raw)
    assert data["confidence"] == 0.8


def test_parse_invalid_raises():
    with pytest.raises(ValueError):
        parse_ai_response("no json here")
    with pytest.raises(ValueError):
        parse_ai_response("")


def test_normalize_ai_data_cleans_feedback():
    outcome = normalize_ai_data(
        {
            "decision": "pass",
            "progress": "substantial",
            "has_learning_content": True,
            "duplicate_assessment": "iterative",
            "confidence": 1.7,
            "brief_feedback": "```\n我是AI，这次有实质进展 202612345678\n```",
            "reasons": ["新增功能", "补充测试"],
        }
    )
    assert outcome.passed is True
    assert outcome.confidence == 1.0
    assert "```" not in outcome.brief_feedback
    assert "202612345678" not in outcome.brief_feedback
    assert outcome.reasons == ["新增功能", "补充测试"]


def test_normalize_rejects_bad_decision():
    with pytest.raises(ValueError):
        normalize_ai_data({"decision": "maybe"})
