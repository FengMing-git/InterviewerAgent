from __future__ import annotations

import os

os.environ.setdefault("LLM_API_KEY", "test-llm-key")
os.environ.setdefault("LLM_MODEL", "test-model")
os.environ.setdefault("LLM_BASE_URL", "http://127.0.0.1:9/v1")
os.environ.setdefault("TAVILY_API_KEY", "test-tavily-key")
