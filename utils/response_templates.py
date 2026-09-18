"""面向 QQ 用户的固定回复模板（「词九」人格）。

设计约束：
- 所有用户可见文案集中在此处，禁止把 LLM 自由生成文本直接作为最终回复。
- 语气分层：成功/提醒强人格；内容未通过温和；管理/技术异常专业；权限拒绝温和坚定。
"""

from __future__ import annotations

import re

PERSONA_NAME = "词九"

_FEEDBACK_MAX_LEN = 80
_MARKDOWN_PATTERNS = (
    re.compile(r"```.*?```", re.S),
    re.compile(r"[`*_#>|]+"),
    re.compile(r"^\s*[-+]\s+", re.M),
)
_SELF_REFERENCE_PATTERNS = (
    re.compile(r"作为(一个)?(AI|人工智能|大模型|语言模型)[^，。；\n]*[，。；]?"),
    re.compile(r"我是(一个)?(AI|人工智能|大模型|语言模型)[^，。；\n]*[，。；]?"),
    re.compile(r"根据(你的|我的|上述)?(规则|要求|设定)[^，。；\n]*[，。；]?"),
)
_LONG_DIGITS = re.compile(r"\d{6,}")


def sanitize_feedback(text: str | None, max_length: int = _FEEDBACK_MAX_LEN) -> str:
    """清洗 AI 简评：去 Markdown、去换行、去自称与身份信息、限长。"""

    if not text:
        return "这次材料词九还需要再确认一下呢"
    value = str(text)
    value = value.replace("\r", "\n").replace("\n", " ")
    for pattern in _MARKDOWN_PATTERNS:
        value = pattern.sub(" ", value)
    for pattern in _SELF_REFERENCE_PATTERNS:
        value = pattern.sub(" ", value)
    value = _LONG_DIGITS.sub("***", value)
    value = re.sub(r"\s+", " ", value).strip(" ，。;；")
    if not value:
        return "这次材料词九还需要再确认一下呢"
    if len(value) > max_length:
        value = value[:max_length].rstrip() + "…"
    return value


def bind_success(name: str = "") -> str:
    return "绑定好啦，同学～词九记住你啦。以后引用 .docx 后发 /d 就能打卡喵。"


def bind_already() -> str:
    return "已经绑定过啦，不用重复来一次呀。要改学号的话请找管理员处理。"


def bind_student_conflict() -> str:
    return "这个学号已经被别的 QQ 绑定啦，词九不能直接改。请联系管理员处理身份冲突哦。"


def bind_invalid(reason: str = "") -> str:
    detail = f"问题在于{reason}。" if reason else ""
    return f"绑定没成功呀。{detail}请按 /d 学号 姓名 的格式再发一次喵。"


def not_bound() -> str:
    return "词九还没找到你的绑定信息呢。先发 /d 学号 姓名，再来打卡呀。"


def no_reply_file() -> str:
    return "词九还没拿到打卡文档呢。请引用一份 .docx，再发 /d 喵。"


def unsupported_format() -> str:
    return "这份不是可用的 .docx 呀。另存为 .docx 后再引用提交就好。"


def file_too_large() -> str:
    return "这份文档太大啦，词九这边存不下。请压缩或拆分后再提交哦。"


def nas_write_failed() -> str:
    return "词九这边保存打卡文件失败了，这次没有计次。请联系管理员检查归档目录。"


def download_failed() -> str:
    return "词九没能把这份文档取下来呢，可能是文件已过期。重新发一次文件再引用、然后发 /d 试试呀。"


def processing_error() -> str:
    return "词九处理这次打卡时遇到了点技术问题，先没有计次。请稍后重试，或联系管理员查看日志。"


def checkin_success_first(feedback: str) -> str:
    return f"打卡成功～本周 1/2。词九看过啦：{sanitize_feedback(feedback)}"


def checkin_success_complete(feedback: str) -> str:
    return f"打卡成功，本周 2/2 啦 (≧▽≦)  {sanitize_feedback(feedback)}"


def checkin_extra() -> str:
    return "这次内容也通过啦～本周已经 2/2，词九会把它保存成额外学习记录，不再重复计次。"


def ai_rejected(feedback: str) -> str:
    return (
        f"这次先不计次呀。词九看到的问题是：{sanitize_feedback(feedback)}。"
        "补清楚实际做了什么、怎么验证，再来一次就好。"
    )


def duplicate_hard() -> str:
    return (
        "这份和你之前交过的内容一致呢，所以这次不能重复计次。"
        "要是同一个项目有新进展，把新增部分写清楚再来喵。"
    )


def duplicate_suspicious(feedback: str) -> str:
    return f"这份和之前材料有点像，词九认真看了看：{sanitize_feedback(feedback)}"


def paused_day(reason: str = "") -> str:
    detail = f"原因：{reason}" if reason else "词九先帮你把今天的打卡收起来啦。"
    return f"今天暂停打卡啦，先不用赶。{detail}"


def paused_week(reason: str = "") -> str:
    detail = f"原因：{reason}" if reason else "本周不按缺卡处理哦。"
    return f"这周已经暂停打卡啦，本周不按缺卡处理。{detail}"


def ai_error() -> str:
    return (
        "文档已经替你保存好啦，但审查服务刚刚出了点问题，所以暂时没计次。"
        "稍后引用原文件再发 /d 就能重试呀。"
    )


def docx_parse_failed() -> str:
    return "这份 .docx 词九打不开呢，可能文件损坏或不是标准 Word 文档。重新导出后再试试呀。"


def already_processed() -> str:
    return "这条消息词九已经处理过啦，不用重复发送喵。"


def no_permission() -> str:
    return "这个命令只有插件管理员能用呀。普通打卡命令可以发 /d help 查看。"


def admin_op_ok(target_display: str, count: int) -> str:
    return f"处理完成：{target_display} 当前周已调整为 {count}/2。"


def admin_op_failed(reason: str = "") -> str:
    detail = reason or "目标或参数不正确"
    return f"没改成功：{detail}。数据没有被修改。"


def admin_last_one() -> str:
    return "没改成功：至少要保留 1 名管理员，不能移除最后一位。数据没有被修改。"


def admin_added(target_qq: str) -> str:
    return f"记好啦，{target_qq} 现在是插件管理员。"


def admin_already(target_qq: str) -> str:
    return f"{target_qq} 本来就是插件管理员呀，不用再加一次。"


def admin_removed(target_qq: str) -> str:
    return f"移除好啦，{target_qq} 不再是插件管理员。"


def admin_not_found_admin(target_qq: str) -> str:
    return f"{target_qq} 不在管理员名单里呢。"


def resume_none() -> str:
    return "现在没有生效中的暂停呀，不用恢复。"


def unknown_command(is_admin: bool = False) -> str:
    if is_admin:
        return "这个子命令词九不认识呢。发 /d admin help 看看管理员命令吧。"
    return "这个子命令词九不认识呢。发 /d help 看看能做什么吧。"


def normal_help() -> str:
    return (
        "词九的打卡小助手在这儿～\n"
        "/d 学号 姓名  首次绑定\n"
        "/d            引用 .docx 后打卡\n"
        "/d help       查看普通帮助\n"
        "\n"
        "一周要完成 2 次有效打卡呀。"
    )


def admin_help() -> str:
    return (
        "词九的管理员命令如下：\n"
        "普通命令\n"
        "/d 学号 姓名         首次绑定\n"
        "/d                   引用 .docx 后打卡\n"
        "/d help              普通帮助\n"
        "\n"
        "管理员命令\n"
        "/d admin add <QQ>     添加管理员\n"
        "/d admin remove <QQ>  移除管理员\n"
        "/d admin help         完整帮助\n"
        "/d add <学号/QQ>      当前周人工 +1\n"
        "/d remove <学号/QQ>   当前周人工 -1\n"
        "/d skip d [原因]      暂停当天\n"
        "/d skip w [原因]      暂停本周\n"
        "/d resume             恢复接收\n"
        "/d get [学号/QQ]      导出 Excel\n"
        "/d stat               当前周统计"
    )


def skip_ok(scope: str, reason: str = "") -> str:
    label = "今天" if scope == "day" else "本周"
    detail = f"原因：{reason}" if reason else ""
    return f"记好啦，{label}暂停接收打卡。{detail}".strip()


def resume_ok() -> str:
    return "恢复好啦～现在可以继续提交打卡喵。"


def export_ready(filename: str) -> str:
    return f"导出好啦，文件是 {filename}。词九这就发给你喵。"


def export_empty() -> str:
    return "还没有可导出的用户或记录呀，等大家绑定打卡后再来试试。"


def export_failed() -> str:
    return "导出没成功呀，词九已经把异常记下来了。稍后再试一次，或联系维护同学。"


def stat_normal(total: int, completed: int, one: int, zero: int) -> str:
    return (
        f"词九统计好啦～本周共 {total} 人：已完成 {completed} 人，1 次 {one} 人，0 次 {zero} 人。"
    )


def stat_paused(total: int, reason: str = "") -> str:
    detail = f"原因：{reason}。" if reason else ""
    return f"本周已暂停打卡，共 {total} 人，本周不计缺卡。{detail}"


def stat_failed() -> str:
    return "统计没成功呀，词九已经把异常记下来了。稍后再试一次喵。"


def target_display(name: str, student_id: str) -> str:
    if name and student_id:
        return f"{name}（{student_id}）"
    return student_id or name or "该同学"
