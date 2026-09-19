"""Tavily 搜索工具可用性验证脚本。

运行方式（项目根目录）：
    uv run test/TestTavilyTool.py

需要在 .env 中配置：
    DEEPSEEK_API_KEY
    TAVILY_API_KEY
可选：
    LLM_MODEL      默认 deepseek-flash
    LLM_BASE_URL   默认 https://api.deepseek.com
    HTTP_PROXY     国内网络访问 Tavily 时可能需要
"""

import os

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from langchain_tavily import TavilySearch

load_dotenv()

# 走 OpenAI 兼容协议接入 DeepSeek：用 langchain-openai，
# 换供应商只改这两个环境变量，代码不动。
model = ChatOpenAI(
    model=os.getenv("LLM_MODEL", "deepseek-flash"),
    base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com"),
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    temperature=0,
)

tool = TavilySearch(max_results=5)


def main() -> None:
    agent = create_agent(model=model, tools=[tool])

    question = "哪个国家举办了2024年欧洲杯？给出来源。"
    for chunk in agent.stream(
        {"messages": [{"role": "user", "content": question}]},
        stream_mode="values",
    ):
        chunk["messages"][-1].pretty_print()


if __name__ == "__main__":
    main()
