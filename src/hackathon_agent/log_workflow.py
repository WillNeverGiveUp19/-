"""LangGraph workflow for analyzing a device's operation logs."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import TypedDict

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool
from langchain.tools import tool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOGS_PATH = PROJECT_ROOT / "data" / "logs" / "device_logs.csv"
MAINTENANCE_PATH = PROJECT_ROOT / "data" / "maintenance_records.json"
DEVICE_ID_PATTERN = re.compile(r"[A-Z]{3}-\d{3}", re.IGNORECASE)

ANALYSIS_PROMPT = """你是设备运行日志分析助手，请使用中文回答。
根据输入中的设备日志、程序统计结果和维护记录，简洁回答用户问题。
准确报告数据覆盖时间、温度和振动趋势、告警记录数及告警时段数，并说明相关维护记录。
统计数字以程序统计结果为准，不要自行改写或推测数值。
只有 status=alert 是数据中明确记录的告警；没有配置温度/振动阈值时，不要把数值趋势自行判定为超标或故障。
日志时间是数据采样时间，不代表实时状态；必须指出日志中最新采样时间。不要编造日志或维护信息。"""


class LogAnalysisState(TypedDict):
    question: str
    device_id: str | None
    error: str
    records: list[dict[str, str]]
    maintenance: dict[str, str] | None
    statistics: dict[str, object]
    report: str


def _statistics(records: list[dict[str, str]]) -> dict[str, object]:
    temperatures = [float(record["temperature"]) for record in records]
    vibrations = [float(record["vibration"]) for record in records]
    alert_count = sum(record["status"] == "alert" for record in records)
    alert_periods = 0
    previous_was_alert = False
    for record in records:
        is_alert = record["status"] == "alert"
        if is_alert and not previous_was_alert:
            alert_periods += 1
        previous_was_alert = is_alert

    def metric(values: list[float]) -> dict[str, float | str]:
        return {
            "first": values[0],
            "last": values[-1],
            "change": values[-1] - values[0],
            "minimum": min(values),
            "maximum": max(values),
            "maximum_at": records[values.index(max(values))]["timestamp"],
        }

    return {
        "period_start": records[0]["timestamp"],
        "period_end": records[-1]["timestamp"],
        "sample_count": len(records),
        "alert_record_count": alert_count,
        "alert_period_count": alert_periods,
        "temperature_c": metric(temperatures),
        "vibration_mm_s": metric(vibrations),
    }


def create_log_analysis_workflow(llm: BaseChatModel) -> CompiledStateGraph:
    """Create a workflow that extracts a device ID, loads its data, and analyzes it."""

    def extract_device_id(state: LogAnalysisState) -> dict[str, str | None]:
        matches = DEVICE_ID_PATTERN.findall(state["question"])
        device_ids = list(dict.fromkeys(match.upper() for match in matches))
        if not device_ids:
            return {
                "device_id": None,
                "error": "请在问题中提供设备编号，例如 DEV-001。",
            }
        if len(device_ids) > 1:
            return {
                "device_id": None,
                "error": f"问题中包含多个设备编号（{', '.join(device_ids)}），请指定一个设备。",
            }
        return {"device_id": device_ids[0], "error": ""}

    def load_device_data(state: LogAnalysisState) -> dict[str, object]:
        device_id = state["device_id"]
        if device_id is None:
            raise RuntimeError("日志查询节点收到空设备编号。")

        with LOGS_PATH.open(encoding="utf-8", newline="") as log_file:
            records = sorted(
                (
                    row
                    for row in csv.DictReader(log_file)
                    if row.get("device_id", "").upper() == device_id
                ),
                key=lambda row: row["timestamp"],
            )

        if not records:
            return {
                "records": [],
                "maintenance": None,
                "statistics": {},
                "error": f"运行日志中没有找到设备 {device_id} 的记录。",
            }

        with MAINTENANCE_PATH.open(encoding="utf-8") as maintenance_file:
            maintenance_records = json.load(maintenance_file)
        maintenance = next(
            (
                record
                for record in maintenance_records
                if record.get("device_id", "").upper() == device_id
            ),
            None,
        )
        return {
            "records": records,
            "maintenance": maintenance,
            "statistics": _statistics(records),
        }

    def analyze(state: LogAnalysisState) -> dict[str, str]:
        analysis_data = {
            "device_id": state["device_id"],
            "statistics": state["statistics"],
            "maintenance_record": state["maintenance"] or "未找到该设备的维护记录",
            "log_records": state["records"],
        }
        response = llm.invoke(
            [
                ("system", ANALYSIS_PROMPT),
                (
                    "human",
                    f"用户问题：{state['question']}\n"
                    f"设备数据：{json.dumps(analysis_data, ensure_ascii=False)}",
                ),
            ]
        )
        content = response.content
        if isinstance(content, str):
            report = content
        elif isinstance(content, list):
            report = "\n".join(
                block["text"]
                for block in content
                if isinstance(block, dict) and isinstance(block.get("text"), str)
            )
        else:
            report = ""
        if not report.strip():
            raise RuntimeError("日志分析模型没有返回文本报告。")
        return {"report": report}

    def finish_with_error(state: LogAnalysisState) -> dict[str, str]:
        return {"report": state["error"]}

    def route_after_extract(state: LogAnalysisState) -> str:
        return "finish_with_error" if state["error"] else "load_device_data"

    def route_after_load(state: LogAnalysisState) -> str:
        return "finish_with_error" if state["error"] else "analyze"

    graph = StateGraph(LogAnalysisState)
    graph.add_node("extract_device_id", extract_device_id)
    graph.add_node("load_device_data", load_device_data)
    graph.add_node("analyze", analyze)
    graph.add_node("finish_with_error", finish_with_error)
    graph.add_edge(START, "extract_device_id")
    graph.add_conditional_edges(
        "extract_device_id",
        route_after_extract,
        {
            "finish_with_error": "finish_with_error",
            "load_device_data": "load_device_data",
        },
    )
    graph.add_conditional_edges(
        "load_device_data",
        route_after_load,
        {"finish_with_error": "finish_with_error", "analyze": "analyze"},
    )
    graph.add_edge("analyze", END)
    graph.add_edge("finish_with_error", END)
    return graph.compile()


def create_log_analysis_tool(llm: BaseChatModel) -> BaseTool:
    """Wrap the device-log workflow as a tool callable by the main agent."""
    workflow = create_log_analysis_workflow(llm)

    @tool
    def analyze_device_logs(question: str) -> str:
        """查询指定设备的运行日志，汇总温度、振动趋势、告警及维护记录。"""
        result = workflow.invoke(
            {
                "question": question,
                "device_id": None,
                "error": "",
                "records": [],
                "maintenance": None,
                "statistics": {},
                "report": "",
            }
        )
        return result["report"]

    return analyze_device_logs
