# astrbot_plugin_direct_checkin

西北大学计算机与网络空间安全协会·软件设计直属队学习打卡 AstrBot 插件。

插件以 QQ（AstrBot `aiocqhttp` / OneBot v11）为基线，提供用户绑定、Word 学习记录归档、重复检测、AI 有效性审查、每周计次、暂停打卡、管理员维护、Excel 导出与统计。

## 功能概览

- 用户绑定学号与姓名（QQ 维度唯一）。
- 引用 `.docx` 后发送 `/d` 打卡，文件归档到 NAS。
- 与历史材料做文件哈希 / 文本哈希 / 相似度查重。
- 调用当前会话聊天模型做宽松但结构化的有效性审查。
- 北京时间自然周内最多计 2 次，超出记为有效额外材料。
- 管理员人工计次、暂停当天/本周、恢复、导出、统计。

## 命令

普通用户：

```text
/d <学号> <名字>    首次绑定
/d                  引用 .docx 后打卡
/d help             普通帮助
```

管理员：

```text
/d admin add <QQ>       添加管理员
/d admin remove <QQ>    移除管理员
/d admin help           完整帮助
/d add <学号/QQ>        当前周人工 +1
/d remove <学号/QQ>     当前周人工 -1
/d skip d [原因]        暂停当天
/d skip w [原因]        暂停本周
/d resume               恢复接收
/d get [学号/QQ]        导出 Excel
/d stat                 当前周统计
```

## 配置

插件配置见 `_conf_schema.json`，可在 AstrBot WebUI 的插件配置页修改。关键项：

- `bootstrap_admin_qq`：初始/恢复管理员 QQ。
- `nas_base_dir`：打卡文件根目录，必须已存在且可写（严格模式）。
- `weekly_limit`、`max_file_size_mb`、`student_id_regex`。
- `similarity_warn_threshold`、`similarity_high_threshold`、`history_compare_weeks`。
- `ai_timeout_seconds`、`ai_retry_count`、`ai_max_input_chars`。

## 文档

- 用户使用文档：`docs/直属队打卡_用户使用文档.md`
- 需求与设计文档：`docs/直属队打卡_AstrBot插件需求文档.md`

## 开发

依赖安装：

```bash
pip install -r requirements.txt
pip install ruff pytest
```

代码检查与格式化：

```bash
ruff format .
ruff check .
```

运行单元测试（无需 AstrBot 运行环境）：

```bash
pytest -q
```

## 参考

- [AstrBot](https://github.com/AstrBotDevs/AstrBot)
- [AstrBot 插件开发文档](https://docs.astrbot.app/dev/star/plugin-new.html)
