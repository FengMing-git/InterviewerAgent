from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_tavily import TavilySearch

load_dotenv()

tool = TavilySearch(
    max_results=5,
    topic="general",
    # include_answer=False,
    # include_raw_content=False,
    # include_images=False,
    # include_image_descriptions=False,
    # search_depth="basic",
    # time_range="day",
    # include_domains=None,
    # exclude_domains=None
)

# 创建一个agent
agent = create_agent(model="deepseek-chat",tools=[tool])

user_input = "哪个国家举办了2024年欧洲杯？给出来源。"

stream = agent.stream_events({"messages": user_input}, version="v3")
for snapshot in stream.values:
    snapshot["messages"][-1].pretty_print()