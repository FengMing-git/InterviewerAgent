"""SSE 事件格式化（Plan §3：core/sse.py；SRS O2 事件类型见 §7）。

手写事件格式（不引 sse-starlette）：
    event: token
    data: {"text": "..."}

data 是 dict/list 就 JSON 序列化（ensure_ascii=False，中文原样推），
是 str 就原样放（此时调用方自己保证不含换行）。
"""

from __future__ import annotations

import json
from typing import Any


def sse_event(event: str, data: Any) -> str:
    payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    # data 里的换行会破坏 SSE 帧，多行文本需先由调用方切成小段
    return f"event: {event}\ndata: {payload}\n\n"
