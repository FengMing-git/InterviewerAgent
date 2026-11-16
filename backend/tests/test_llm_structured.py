"""structured_generate（agent/llm.py）单元测试：纯逻辑，不依赖数据库。"""

from __future__ import annotations

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from pydantic import BaseModel

from app.agent.llm import structured_generate
from app.core.errors import AppError


class Sample(BaseModel):
    name: str
    count: int = 0


def _ai(content: str, **usage: int) -> AIMessage:
    if usage:
        usage.setdefault("total_tokens", usage.get("input_tokens", 0) + usage.get("output_tokens", 0))
        return AIMessage(content=content, usage_metadata=usage)
    return AIMessage(content=content)


def _fake(*messages: AIMessage) -> GenericFakeChatModel:
    return GenericFakeChatModel(messages=iter(messages))


def test_plain_json():
    result = structured_generate(_fake(_ai('{"name": "简历"}', input_tokens=10, output_tokens=5)), "s", "u", Sample)
    assert result.parsed.name == "简历"
    assert result.input_tokens == 10 and result.output_tokens == 5


def test_fenced_and_surrounding_prose():
    # 代码围栏 + 前后说明文字都要能剥掉
    llm = _fake(
        _ai("好的，结果是：\n```json\n{\"name\": \"x\", \"count\": 3}\n```\n以上。"),
        _ai("说明 {\"name\": \"y\"} 结束"),
    )
    assert structured_generate(llm, "s", "u", Sample).parsed.count == 3
    assert structured_generate(llm, "s", "u", Sample).parsed.name == "y"


def test_retry_with_validation_error_then_success():
    # 第一次类型错误 → 带错误重试 → 第二次合法（Plan §5）
    llm = _fake(_ai('{"name": 123, "count": "x"}'), _ai('{"name": "ok"}', input_tokens=2, output_tokens=2))
    result = structured_generate(llm, "s", "u", Sample, purpose="样例")
    assert result.parsed.name == "ok"
    assert result.input_tokens == 2 and result.output_tokens == 2


def test_double_failure_raises_apperror():
    llm = _fake(_ai("完全不是 JSON"), _ai('{"name": []}'))
    with pytest.raises(AppError) as exc_info:
        structured_generate(llm, "s", "u", Sample)
    assert exc_info.value.code == 50000
    assert "无法解析" in exc_info.value.message


def test_usage_accumulates_across_retry():
    llm = _fake(
        _ai("呃", input_tokens=3, output_tokens=1),
        _ai('{"name": "z"}', input_tokens=5, output_tokens=2),
    )
    result = structured_generate(llm, "s", "u", Sample)
    assert result.input_tokens == 8 and result.output_tokens == 3
