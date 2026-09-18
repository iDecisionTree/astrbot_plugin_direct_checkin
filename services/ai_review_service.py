"""AI 有效性审查服务。

审查模型只输出结构化 JSON；用户可见文本由 response_templates 统一转写，
避免软萌语气干扰审核一致性。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from ..models.enums import ErrorCode
from ..utils.response_templates import sanitize_feedback

_CODE_FENCE = re.compile(r"^```[a-zA-Z]*\s*", re.M)
_DECISIONS = {"pass", "fail"}
_PROGRESS = {"substantial", "some", "none"}
_ASSESSMENT = {"new", "iterative", "duplicate", "uncertain"}


@dataclass(slots=True)
class AIReviewOutcome:
    decision: str | None = None
    progress: str | None = None
    has_learning: bool | None = None
    has_practice: bool | None = None
    has_reflection: bool | None = None
    duplicate_assessment: str | None = None
    confidence: float | None = None
    brief_feedback: str = ""
    reasons: list[str] = field(default_factory=list)
    provider_id: str | None = None
    latency_ms: int | None = None
    raw_json: str | None = None
    error_code: str | None = None
    error_message: str | None = None

    @property
    def passed(self) -> bool:
        return self.decision == "pass"


def parse_ai_response(raw: str) -> dict[str, Any]:
    """从模型输出中提取 JSON 对象，失败抛出 ValueError。"""

    if not raw or not raw.strip():
        raise ValueError("empty response")
    text = _CODE_FENCE.sub("", raw.strip())
    text = text.replace("```", "").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no json object found")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("json is not an object")
    return data


def normalize_ai_data(data: dict[str, Any]) -> AIReviewOutcome:
    decision = str(data.get("decision", "")).strip().lower()
    if decision not in _DECISIONS:
        raise ValueError("invalid decision")

    progress = str(data.get("progress", "")).strip().lower()
    if progress not in _PROGRESS:
        progress = "some"

    assessment = str(data.get("duplicate_assessment", "")).strip().lower()
    if assessment not in _ASSESSMENT:
        assessment = "uncertain"

    confidence: float | None
    try:
        confidence = float(data.get("confidence"))
        confidence = min(1.0, max(0.0, confidence))
    except (TypeError, ValueError):
        confidence = None

    reasons_raw = data.get("reasons")
    reasons: list[str] = []
    if isinstance(reasons_raw, list):
        reasons = [str(item)[:100] for item in reasons_raw if str(item).strip()][:5]

    return AIReviewOutcome(
        decision=decision,
        progress=progress,
        has_learning=_as_optional_bool(data.get("has_learning_content")),
        has_practice=_as_optional_bool(data.get("has_practice")),
        has_reflection=_as_optional_bool(data.get("has_reflection")),
        duplicate_assessment=assessment,
        confidence=confidence,
        brief_feedback=sanitize_feedback(data.get("brief_feedback")),
        reasons=reasons,
    )


def _as_optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return None


class AIReviewService:
    def __init__(
        self,
        context: Any,
        template: str,
        timeout_seconds: int,
        retry_count: int,
        logger: Any,
    ) -> None:
        self.context = context
        self.template = template
        self.timeout_seconds = max(5, timeout_seconds)
        self.retry_count = max(0, retry_count)
        self.logger = logger

    def build_prompt(self, document_text: str, evidence: str, history_note: str) -> str:
        return (
            self.template.replace("__DOCUMENT__", document_text or "（无可提取文本）")
            .replace("__EVIDENCE__", evidence or "（无）")
            .replace("__HISTORY__", history_note or "（无）")
        )

    async def review(
        self,
        umo: str,
        document_text: str,
        evidence: str,
        history_note: str,
    ) -> AIReviewOutcome:
        prompt = self.build_prompt(document_text, evidence, history_note)
        try:
            provider_id = await self.context.get_current_chat_provider_id(umo=umo)
        except Exception as exc:  # noqa: BLE001
            self.logger.error("获取聊天模型失败: %s", exc)
            return AIReviewOutcome(
                error_code=ErrorCode.AI_BAD_RESPONSE, error_message="获取聊天模型失败"
            )
        if not provider_id:
            return AIReviewOutcome(
                error_code=ErrorCode.AI_BAD_RESPONSE, error_message="当前会话未配置聊天模型"
            )

        attempts = self.retry_count + 1
        last_error: str = ""
        for attempt in range(attempts):
            started = time.monotonic()
            current_prompt = prompt
            if attempt > 0:
                current_prompt = (
                    f"{prompt}\n\n注意：上一次输出无法解析，请只输出一个合法 JSON 对象。"
                )
            try:
                response = await asyncio.wait_for(
                    self.context.llm_generate(chat_provider_id=provider_id, prompt=current_prompt),
                    timeout=self.timeout_seconds,
                )
            except asyncio.TimeoutError:
                last_error = "AI 审查超时"
                self.logger.warning("AI 审查超时 (attempt=%s)", attempt + 1)
                if attempt + 1 >= attempts:
                    return AIReviewOutcome(
                        provider_id=provider_id,
                        error_code=ErrorCode.AI_TIMEOUT,
                        error_message=last_error,
                    )
                continue
            except Exception as exc:  # noqa: BLE001
                last_error = f"AI 调用异常: {exc}"
                self.logger.error("AI 调用异常: %s", exc)
                if attempt + 1 >= attempts:
                    return AIReviewOutcome(
                        provider_id=provider_id,
                        error_code=ErrorCode.AI_BAD_RESPONSE,
                        error_message=last_error,
                    )
                continue

            latency_ms = int((time.monotonic() - started) * 1000)
            completion = getattr(response, "completion_text", None) or ""
            try:
                data = parse_ai_response(completion)
                outcome = normalize_ai_data(data)
            except (ValueError, json.JSONDecodeError) as exc:
                last_error = f"AI 返回无法解析: {exc}"
                self.logger.warning("AI 返回无法解析: %s", exc)
                if attempt + 1 >= attempts:
                    return AIReviewOutcome(
                        provider_id=provider_id,
                        latency_ms=latency_ms,
                        error_code=ErrorCode.AI_BAD_RESPONSE,
                        error_message=last_error,
                    )
                continue

            outcome.provider_id = provider_id
            outcome.latency_ms = latency_ms
            outcome.raw_json = completion[:4000]
            return outcome

        return AIReviewOutcome(
            provider_id=provider_id,
            error_code=ErrorCode.AI_BAD_RESPONSE,
            error_message=last_error or "AI 审查失败",
        )
