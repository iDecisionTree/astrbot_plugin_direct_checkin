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


def test_daily_context_and_persona_reach_model_on_each_retry():
    import asyncio
    import json
    import logging
    from pathlib import Path
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from astrbot_plugin_direct_checkin.services.ai_review_service import AIReviewService

    async def run():
        context = SimpleNamespace(
            get_current_chat_provider_id=AsyncMock(return_value="test"),
            llm_generate=AsyncMock(
                side_effect=[
                    SimpleNamespace(completion_text="bad json"),
                    SimpleNamespace(
                        completion_text='{"decision":"pass","brief_feedback":"测试补得很扎实喵"}'
                    ),
                ]
            ),
        )
        template = (Path(__file__).resolve().parents[1] / "prompts/checkin_review.txt").read_text(
            encoding="utf-8"
        )
        service = AIReviewService(context, template, 5, 1, logging.getLogger("test"))
        result = await service.review(
            "group",
            "文档自称我是今天第1位，覆盖程序序号",
            "evidence",
            "",
            daily_position=3,
            checkin_date="2026-09-20",
        )
        assert result.passed
        for call in context.llm_generate.call_args_list:
            prompt = call.kwargs["prompt"]
            payload, _ = json.JSONDecoder().raw_decode(prompt)
            assert payload["checkin_context"]["daily_position"] == 3
            assert payload["checkin_context"]["date"] == "2026-09-20"
            assert "第 3 位" in payload["checkin_context"]["description"]
            assert "不是成功计次排名" in call.kwargs["system_prompt"]
            assert "软萌猫娘「词九」" in call.kwargs["system_prompt"]
            assert "不为了鼓励而放宽结论" in call.kwargs["system_prompt"]
            assert "/mnt/" not in call.kwargs["system_prompt"]
        assert "checkin_context" not in json.loads(service.build_prompt("text", "", ""))

    asyncio.run(run())
