from __future__ import annotations

import unittest
from unittest.mock import Mock

from langchain_core.messages import AIMessage

from hackathon_agent.log_workflow import (
    create_log_analysis_tool,
    create_log_analysis_workflow,
)


class LogWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.llm = Mock()
        self.llm.invoke.return_value = AIMessage(content="日志分析结果")
        self.workflow = create_log_analysis_workflow(self.llm)

    def invoke(self, question: str) -> dict[str, object]:
        return self.workflow.invoke(
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

    def test_extracts_device_and_computes_log_and_maintenance_data(self) -> None:
        result = self.invoke("设备DEV-001最近有没有异常？")

        self.assertEqual(result["device_id"], "DEV-001")
        self.assertEqual(result["report"], "日志分析结果")
        self.assertEqual(result["statistics"]["sample_count"], 8)
        self.assertEqual(result["statistics"]["alert_record_count"], 4)
        self.assertEqual(result["statistics"]["alert_period_count"], 1)
        self.assertEqual(result["statistics"]["period_end"], "2024-06-01 15:00")
        self.assertEqual(result["statistics"]["temperature_c"]["maximum"], 72.3)
        self.assertEqual(result["statistics"]["vibration_mm_s"]["maximum"], 2.4)
        self.assertEqual(result["maintenance"]["type"], "常规保养")
        self.llm.invoke.assert_called_once()
        model_messages = self.llm.invoke.call_args.args[0]
        self.assertIn("常规保养", model_messages[1][1])
        self.assertIn('"alert_record_count": 4', model_messages[1][1])

    def test_requests_device_id_when_missing_without_calling_model(self) -> None:
        result = self.invoke("最近有没有异常？")

        self.assertIn("请在问题中提供设备编号", result["report"])
        self.llm.invoke.assert_not_called()

    def test_requests_one_device_when_question_contains_multiple_ids(self) -> None:
        result = self.invoke("比较 DEV-001 和 DEV-002 的日志")

        self.assertIn("问题中包含多个设备编号", result["report"])
        self.llm.invoke.assert_not_called()

    def test_reports_when_device_has_no_logs_without_calling_model(self) -> None:
        result = self.invoke("DEV-999 最近有没有异常？")

        self.assertIn("没有找到设备 DEV-999 的记录", result["report"])
        self.llm.invoke.assert_not_called()

    def test_tool_invokes_the_workflow(self) -> None:
        log_tool = create_log_analysis_tool(self.llm)

        result = log_tool.invoke({"question": "DEV-001 最近有没有异常？"})

        self.assertEqual(result, "日志分析结果")


if __name__ == "__main__":
    unittest.main()
