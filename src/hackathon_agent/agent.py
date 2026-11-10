"""LangChain agent for answering maintenance questions with manual retrieval."""

from __future__ import annotations

import atexit
import os
from threading import Lock
from pathlib import Path

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_core.tools import BaseTool
from langchain.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph.state import CompiledStateGraph
from openai import OpenAI
from pymilvus import MilvusClient
from pymilvus.exceptions import ConnectionConfigException

from hackathon_agent.log_workflow import create_log_analysis_tool

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATABASE_PATH = PROJECT_ROOT / "data" / "milvus_lite.db"
COLLECTION_NAME = "manuals"
EMBEDDING_MODEL = "qwen3.7-text-embedding"
AGENT_MODEL = "qwen3.8-max"
RETRIEVAL_LIMIT = 4

_MILVUS_LOCK = Lock()
_MILVUS_CLIENT: MilvusClient | None = None

SYSTEM_PROMPT = """你是设备维护知识助手。请使用中文回答。
遇到设备故障、故障码或维修步骤问题时，必须先调用 search_manuals 工具检索手册，
再基于检索结果回答。清楚区分手册中明确记载的内容和你的推断；
如果检索结果不足以回答，请明确说明，不要编造手册内容。回答时注明引用的手册文件名。
遇到设备运行日志、温度/振动趋势或告警统计问题时，调用 analyze_device_logs 工具；
日志报告基于数据文件中的历史采样，不代表实时状态。"""


def _close_milvus_client() -> None:
    global _MILVUS_CLIENT
    with _MILVUS_LOCK:
        if _MILVUS_CLIENT is not None:
            _MILVUS_CLIENT.close()
            _MILVUS_CLIENT = None


atexit.register(_close_milvus_client)


def _create_retrieval_tool(api_key: str, base_url: str) -> BaseTool:
    @tool
    def search_manuals(query: str) -> str:
        """从本地 Milvus Lite 手册向量库检索设备故障与维护信息。"""
        if not DATABASE_PATH.exists():
            return (
                "本地向量数据库不存在。请先运行 "
                "`uv run python -m hackathon_agent.ingest_manuals` 导入手册。"
            )

        with OpenAI(api_key=api_key, base_url=base_url) as embedding_client:
            embedding_response = embedding_client.embeddings.create(
                model=EMBEDDING_MODEL,
                input=[query],
            )
        if not embedding_response.data:
            raise RuntimeError("Embedding API 没有为检索问题返回向量。")
        query_vector = embedding_response.data[0].embedding

        global _MILVUS_CLIENT
        with _MILVUS_LOCK:
            try:
                if _MILVUS_CLIENT is None:
                    _MILVUS_CLIENT = MilvusClient(str(DATABASE_PATH))
            except ConnectionConfigException as exc:
                raise RuntimeError(
                    "无法打开 Milvus Lite 本地数据库；它正被其他进程占用。请关闭其他 "
                    "Agent、导入脚本或实际连接该数据库的 Python 进程后重试。"
                ) from exc

            if not _MILVUS_CLIENT.has_collection(COLLECTION_NAME):
                return (
                    "本地 Milvus 中尚无 manuals 集合。请先运行 "
                    "`uv run python -m hackathon_agent.ingest_manuals` 导入手册。"
                )

            _MILVUS_CLIENT.load_collection(collection_name=COLLECTION_NAME)
            results = _MILVUS_CLIENT.search(
                collection_name=COLLECTION_NAME,
                data=[query_vector],
                anns_field="vector",
                limit=RETRIEVAL_LIMIT,
                output_fields=["text", "source_file", "manual_title"],
            )

        if not results or not results[0]:
            return "手册向量库中没有找到相关内容。"

        excerpts = []
        for rank, hit in enumerate(results[0], start=1):
            entity = hit["entity"]
            excerpts.append(
                f"[{rank}] 来源：{entity['source_file']} / {entity['manual_title']}\n"
                f"相似度：{hit['distance']:.4f}\n"
                f"内容：{entity['text']}"
            )
        return "\n\n".join(excerpts)

    return search_manuals


def create_manual_agent() -> CompiledStateGraph:
    """Create the Qwen agent and its Milvus-backed manual search tool."""
    load_dotenv(PROJECT_ROOT / ".env")
    tracing_enabled = os.getenv("LANGSMITH_TRACING", "").strip().lower() in {
        "true",
        "1",
        "yes",
        "on",
    }
    if tracing_enabled and not os.getenv("LANGSMITH_API_KEY"):
        raise RuntimeError(
            "已启用 LangSmith tracing，但未配置 LANGSMITH_API_KEY。"
            "请在项目根目录 .env 中设置 LangSmith API Key。"
        )

    api_key = os.getenv("DASHSCOPE_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "未配置 API Key，请在项目根目录 .env 中设置 DASHSCOPE_API_KEY。"
        )

    base_url = (
        os.getenv("DASHSCOPE_BASE_URL")
        or os.getenv("OPENAI_BASE_URL")
        or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    llm = ChatOpenAI(
        model=AGENT_MODEL,
        api_key=api_key,
        base_url=base_url,
        temperature=0,
    )
    retrieval_tool = _create_retrieval_tool(api_key, base_url)
    log_analysis_tool = create_log_analysis_tool(llm)
    return create_agent(
        model=llm,
        tools=[retrieval_tool, log_analysis_tool],
        system_prompt=SYSTEM_PROMPT,
        name="maintenance_manual_agent",
    )


def main() -> None:
    agent = create_manual_agent()
    print(f"设备维护助手已启动（{AGENT_MODEL}），输入 /quit 退出。")
    if os.getenv("LANGSMITH_TRACING", "").strip().lower() in {
        "true",
        "1",
        "yes",
        "on",
    }:
        print(
            "LangSmith tracing 已启用，项目："
            f"{os.getenv('LANGSMITH_PROJECT', 'default')}"
        )

    while True:
        try:
            question = input("\n你：").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return

        if question.lower() in {"/quit", "/exit"}:
            return
        if not question:
            continue

        result = agent.invoke(
            {"messages": [{"role": "user", "content": question}]}
        )
        print(f"\n助手：{result['messages'][-1].content}")


if __name__ == "__main__":
    main()
