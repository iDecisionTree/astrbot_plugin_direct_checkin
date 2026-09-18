from astrbot_plugin_direct_checkin.services.command_router import CommandName, parse_command


def test_checkin_without_args():
    assert parse_command("/d").name == CommandName.CHECKIN
    assert parse_command("d").name == CommandName.CHECKIN
    assert parse_command("  /d  ").name == CommandName.CHECKIN


def test_bind_two_args():
    cmd = parse_command("/d 2026123456 张三")
    assert cmd.name == CommandName.BIND
    assert cmd.args == ["2026123456", "张三"]


def test_help():
    assert parse_command("/d help").name == CommandName.HELP


def test_admin_subcommands():
    cmd = parse_command("/d admin add 123456789")
    assert cmd.name == CommandName.ADMIN
    assert cmd.args == ["add", "123456789"]
    assert parse_command("/d admin help").args == ["help"]
    assert parse_command("/d admin").args == ["help"]
    assert parse_command("/d admin weird").name == CommandName.UNKNOWN


def test_skip_and_resume():
    cmd = parse_command("/d skip w 期中考试周")
    assert cmd.name == CommandName.SKIP
    assert cmd.args == ["w"]
    assert cmd.reason == "期中考试周"
    assert parse_command("/d skip d").args == ["d"]
    assert parse_command("/d resume").name == CommandName.RESUME


def test_add_remove_get_stat():
    assert parse_command("/d add 2026123456").name == CommandName.ADD
    assert parse_command("/d remove 2026123456").name == CommandName.REMOVE
    assert parse_command("/d get").name == CommandName.GET
    assert parse_command("/d get 2026123456").target == "2026123456"
    assert parse_command("/d stat").name == CommandName.STAT


def test_unknown_subcommands():
    assert parse_command("/d 开挂").name == CommandName.UNKNOWN
    assert parse_command("/d addx 123").name == CommandName.UNKNOWN


def test_non_d_command_not_matched():
    assert parse_command("/hello").name == CommandName.UNKNOWN
    assert parse_command("").name == CommandName.UNKNOWN
