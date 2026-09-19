from astrbot_plugin_direct_checkin.utils import response_templates as T


def test_sanitize_feedback_removes_markdown_and_identity():
    raw = "```json\n我是AI，根据你的规则：学号 202612345678 这次实践不错\n```"
    cleaned = T.sanitize_feedback(raw)
    assert "```" not in cleaned
    assert "202612345678" not in cleaned
    assert "我是AI" not in cleaned
    assert "\n" not in cleaned


def test_sanitize_feedback_truncates():
    cleaned = T.sanitize_feedback("存" * 300)
    assert len(cleaned) <= 81
    assert cleaned.endswith("…")


def test_persona_present_in_key_templates():
    assert "词九" in T.bind_success("张三")
    assert "词九" in T.not_bound()
    assert "词九" in T.normal_help()
    assert "管理员命令" in T.admin_help()


def test_normal_help_does_not_leak_admin_commands():
    help_text = T.normal_help()
    assert "admin" not in help_text
    assert "/d add" not in help_text


def test_count_templates():
    assert "1/2" in T.checkin_success_first("有进展")
    assert "2/2" in T.checkin_success_complete("有进展")
    assert "额外" in T.checkin_extra()


def test_daily_greeting_preserves_counted_and_extra_semantics():
    counted = T.checkin_success("异常测试补得很清楚喵", 2, 3, 7)
    assert "第 7 位" in counted and "2/3" in counted
    assert "本周已完成" not in counted
    completed = T.checkin_success("有进展", 3, 3, 7)
    assert "本周已完成" in completed and "3/3" in completed
    for extra in (T.checkin_extra(3, 7), T.checkin_extra_same_day(7)):
        assert "第 7 位" in extra and "额外材料" in extra and "不增加计次" in extra
    assert "第 None" not in T.checkin_success("有进展", 1, 2)
    assert "计次" in T.ai_rejected("请补充测试结果呀")
