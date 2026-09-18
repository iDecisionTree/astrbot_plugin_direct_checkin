"""``/d`` 命令解析。

只注册根命令 ``/d``，其余参数在插件内部严格解析，避免指令组路由歧义。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


class CommandName:
    CHECKIN = "checkin"
    BIND = "bind"
    HELP = "help"
    ADMIN = "admin"
    ADD = "add"
    REMOVE = "remove"
    SKIP = "skip"
    RESUME = "resume"
    GET = "get"
    STAT = "stat"
    UNKNOWN = "unknown"


_ROOT_PATTERN = re.compile(r"^[/／]?\s*d(?=\s|$)", re.IGNORECASE)
_DIGITS = re.compile(r"^\d+$")

_ADMIN_SUBCOMMANDS = {"add", "remove", "help"}


@dataclass(slots=True)
class ParsedCommand:
    name: str
    args: list[str] = field(default_factory=list)
    reason: str = ""

    @property
    def target(self) -> str:
        return self.args[0] if self.args else ""

    def is_admin_command(self) -> bool:
        return self.name in {
            CommandName.ADMIN,
            CommandName.ADD,
            CommandName.REMOVE,
            CommandName.SKIP,
            CommandName.RESUME,
            CommandName.GET,
            CommandName.STAT,
        }


def parse_command(raw_text: str) -> ParsedCommand:
    """解析 ``/d`` 及其参数。

    无法识别时返回 ``UNKNOWN``，由上层返回对应级别的帮助。
    """

    text = (raw_text or "").strip()
    match = _ROOT_PATTERN.match(text)
    if not match:
        return ParsedCommand(CommandName.UNKNOWN)
    tail = text[match.end() :].strip()
    if not tail:
        return ParsedCommand(CommandName.CHECKIN)

    tokens = tail.split()
    head = tokens[0].lower()
    rest = tokens[1:]

    if head == "help":
        return ParsedCommand(CommandName.HELP)

    if head == "admin":
        sub = rest[0].lower() if rest else "help"
        if sub not in _ADMIN_SUBCOMMANDS:
            return ParsedCommand(CommandName.UNKNOWN)
        return ParsedCommand(CommandName.ADMIN, args=[sub, *rest[1:]])

    if head == "add":
        return ParsedCommand(CommandName.ADD, args=rest)

    if head == "remove":
        return ParsedCommand(CommandName.REMOVE, args=rest)

    if head == "skip":
        if not rest or rest[0].lower() not in {"d", "w"}:
            return ParsedCommand(CommandName.UNKNOWN)
        reason = " ".join(rest[1:]).strip()
        return ParsedCommand(CommandName.SKIP, args=[rest[0].lower()], reason=reason)

    if head == "resume":
        return ParsedCommand(CommandName.RESUME)

    if head == "get":
        return ParsedCommand(CommandName.GET, args=rest)

    if head == "stat":
        return ParsedCommand(CommandName.STAT)

    # 绑定：/d <学号> <名字>
    if _DIGITS.match(tokens[0]):
        name = " ".join(rest).strip()
        return ParsedCommand(CommandName.BIND, args=[tokens[0], name])

    return ParsedCommand(CommandName.UNKNOWN)
