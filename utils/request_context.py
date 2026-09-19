"""一次事件的稳定身份和接收时间。"""

from __future__ import annotations

import hashlib
import json
from contextvars import ContextVar
from datetime import datetime

received_at: ContextVar[datetime | None] = ContextVar("checkin_received_at", default=None)
event_identity: ContextVar[str | None] = ContextVar("checkin_event_identity", default=None)


def event_key(event) -> str | None:
    message_id = str(getattr(event.message_obj, "message_id", "") or "")
    bot_id = str(getattr(event.message_obj, "self_id", "") or "")
    origin = str(getattr(event, "unified_msg_origin", "") or "")
    if not message_id or not bot_id or not origin:
        return None
    payload = ["aiocqhttp", bot_id, origin, message_id]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()
