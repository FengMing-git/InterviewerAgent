"""LLM 适配层（Plan §6.6 / 模块 M13）。

两件事：
  * ``get_llm(user, purpose)`` —— 取一个配好参数的 ChatOpenAI：
    用户自定义模型配置优先（key 现场解密，失败回落项目密钥并告警），
    否则用项目密钥；统一 timeout / max_retries，按用途设 temperature。
  * ``structured_generate(...)`` —— 结构化输出的统一入口：
    提示词 + Pydantic 模型 → 约束模型只回 JSON → 解析校验；
    校验不通过把错误喂回模型重新生成一次（Plan §5），
    仍失败抛 AppError，由调用方把任务置 failed。

说明：这里没有直接用 ``with_structured_output``，因为它一次失败即抛异常、
无法带错误信息重试；手写「JSON 约束 + Pydantic 校验 + 带错重试」与之等价，
且对任何 OpenAI 兼容网关和测试假模型都通用（Plan §5 的重试要求也一并满足）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ValidationError

from app.config import LLM_MAX_RETRIES, LLM_TIMEOUT, settings
from app.core.errors import INTERNAL, AppError
from app.core.logger import get_logger
from app.core.security import decrypt_secret
from app.models.orm import User

log = get_logger(__name__)

# 各用途 temperature（Plan §6.6）：解析和核验用 0，提问用 0.7，报告用 0.2；
# init（P2 人设与提问方向）取 0.5：既要有风格多样性，又不能太发散
PURPOSE_TEMPERATURES: dict[str, float] = {
    "parse": 0.0,
    "verify": 0.0,
    "init": 0.5,
    "question": 0.7,
    "report": 0.2,
}

_KNOWN_PURPOSES = frozenset(PURPOSE_TEMPERATURES) | {"analyze"}


def get_llm(user: User | None = None, purpose: str = "parse", streaming: bool = False) -> ChatOpenAI:
    """按用途构造 LLM 实例；自定义配置优先（P5 开放配置入口，这里先支持读取）。"""
    if purpose not in _KNOWN_PURPOSES:
        # 未知用途按 parse 的保守参数处理，但留日志方便发现笔误
        log.warning("未知 LLM 用途 %r，按 parse 参数处理", purpose)
        purpose = "parse"

    api_key, base_url, model = settings.llm_api_key, settings.llm_base_url, settings.llm_model
    if user is not None and user.has_custom_llm:
        try:
            api_key = decrypt_secret(user.llm_key_cipher, user.llm_key_nonce)
            base_url, model = user.llm_base_url, user.llm_model
            log.info("使用用户自定义模型配置：user_id=%s model=%s", user.id, model)
        except Exception as exc:  # noqa: BLE001 —— 解密失败回落项目密钥
            log.warning("自定义 API Key 解密失败，回落项目密钥：user_id=%s err=%s", user.id, exc)

    return ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=PURPOSE_TEMPERATURES.get(purpose, 0.0),
        timeout=LLM_TIMEOUT,
        max_retries=LLM_MAX_RETRIES,
        streaming=streaming,
    )


@dataclass(slots=True)
class StructuredOutput:
    """structured_generate 的结果：解析后的对象 + 本次累计的 token 用量。"""

    parsed: Any
    input_tokens: int
    output_tokens: int


def structured_generate(
    llm: Any,
    system_prompt: str,
    user_content: str,
    schema: type[BaseModel],
    *,
    max_attempts: int = 2,
    purpose: str = "结构化输出",
) -> StructuredOutput:
    """让 LLM 按 ``schema`` 产出结构化数据，带一次「校验失败→喂回错误重试」。

    * max_attempts：含首次在内的总尝试次数（Plan §5：重试一次，默认 2）；
    * 返回 ``StructuredOutput``，token 用量从 usage_metadata 累计；
    * 两次都失败抛 AppError(50000)，调用方捕获后把任务置 failed。
    """
    messages: list[BaseMessage] = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=_json_instruction(schema) + "\n\n" + user_content),
    ]
    input_tokens = 0
    output_tokens = 0
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        message = llm.invoke(messages)
        if not isinstance(message, AIMessage):
            message = AIMessage(content=str(getattr(message, "content", "")))
        usage = message.usage_metadata or {}
        input_tokens += int(usage.get("input_tokens") or 0)
        output_tokens += int(usage.get("output_tokens") or 0)

        try:
            data = _parse_json_payload(_message_text(message))
            parsed = schema.model_validate(data)
            return StructuredOutput(parsed=parsed, input_tokens=input_tokens, output_tokens=output_tokens)
        except (ValueError, ValidationError) as exc:
            last_error = exc
            if attempt < max_attempts:
                log.info(
                    "%s 第 %d 次输出未通过校验，把错误喂回模型重试（%s）",
                    purpose,
                    attempt,
                    _brief(str(exc)),
                )
                # 带着错误信息重新生成（Plan §5）
                messages = [
                    *messages,
                    message,
                    HumanMessage(
                        content=(
                            f"上面的输出不满足要求：{_brief(str(exc))}。\n"
                            "请严格按要求重新输出，只输出一个 JSON 对象，"
                            "不要任何解释、前后缀或 Markdown 代码块。"
                        )
                    ),
                ]

    raise AppError(
        INTERNAL,
        f"{purpose}失败：模型多次输出均无法解析为要求的结构，请稍后重试",
        detail=_brief(str(last_error)) if last_error else None,
        status=500,
    )


def _json_instruction(schema: type[BaseModel]) -> str:
    """拼在用户内容最前面的 JSON 结构约束。"""
    return (
        "【输出格式】只输出一个 JSON 对象，不要包含解释文字、前后缀或 Markdown 代码块。\n"
        f"JSON 必须符合以下 JSON Schema（字段可空时用 null，不要省略必需字段）：\n"
        f"{json.dumps(schema.model_json_schema(), ensure_ascii=False)}"
    )


def _message_text(message: AIMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    # 多模态分片形态：拼接所有文本块
    return "".join(part.get("text", "") for part in content if isinstance(part, dict))


def _parse_json_payload(text: str) -> Any:
    """宽容解析模型输出：剥掉代码围栏、截取首尾大括号之间再 json.loads。"""
    stripped = text.strip()
    if stripped.startswith("```"):
        # ```json\n{...}\n``` → 取围栏内
        first_newline = stripped.find("\n")
        stripped = stripped[first_newline + 1 :] if first_newline != -1 else ""
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3]
        stripped = stripped.strip()

    if not stripped.startswith("{"):
        # 模型可能前后加了说明文字：截取第一个 { 到最后一个 }
        start, end = stripped.find("{"), stripped.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("输出中没有 JSON 对象")
        stripped = stripped[start : end + 1]
    return json.loads(stripped)


def _brief(text: str, limit: int = 300) -> str:
    """错误信息截断，避免把超长校验错误塞回提示词。"""
    text = " ".join(text.split())
    return text[:limit] + ("…" if len(text) > limit else "")
