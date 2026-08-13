import sys
from pathlib import Path
from decimal import Decimal
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from depreciation_poc.domain.models import Month, SummaryLine
from depreciation_poc.harness.service import (
    ControlledExplanationHarness,
    ExplanationHarness,
    HarnessTool,
    ToolInvocation,
    ToolRegistry,
)


class EchoProvider:
    provider_name = "echo"

    def explain(self, context):
        return {
            "provider": self.provider_name,
            "summary": f"{len(context['tool_trace'])} tools, {len(context['available_actions'])} actions",
            "key_reasons": [context["drivers"][0]["driver"]],
            "risks": [context["anomalies"][0]["message_cn"]],
            "next_steps": [context["available_functions"][0]["label_cn"]],
        }


def make_controlled_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register_many([
        HarnessTool(
            name="queryDashboard",
            label_cn="读取预算总览",
            description_cn="读取 dashboard",
            read_only=True,
            runner=lambda _args: {"kpis": {"total_depreciation": "100.00"}},
        ),
        HarnessTool(
            name="explainChange",
            label_cn="读取变化解释事实",
            description_cn="读取 driver",
            read_only=True,
            runner=lambda _args: {
                "drivers": [{"driver": "ADDITION", "depreciation": "100.00"}],
                "contributors": [{"asset_ref": "ASSET-TEST-001", "asset_category": "TEST_CATEGORY"}],
            },
        ),
        HarnessTool(
            name="listAnomalies",
            label_cn="读取异常清单",
            description_cn="读取 anomalies",
            read_only=True,
            runner=lambda _args: [{"message_cn": "无阻断异常"}],
        ),
        HarnessTool(
            name="listAssetCards",
            label_cn="读取资产卡片",
            description_cn="读取 cards",
            read_only=True,
            runner=lambda _args: [
                {
                    "asset_ref": "ASSET-TEST-001",
                    "asset_category": "TEST_CATEGORY",
                    "asset_category_label_cn": "测试类别",
                    "depreciation_policy": "POLICY-TEST",
                    "depreciation_policy_label_cn": "测试折旧政策",
                }
            ],
        ),
        HarnessTool(
            name="ontologyMeta",
            label_cn="读取 ontology 元模型",
            description_cn="读取 actions/functions",
            read_only=True,
            runner=lambda _args: {
                "action_types": [{"type_id": "changePlannedAssetAmount", "label_cn": "调整计划资产金额"}],
                "function_types": [{"type_id": "summarizeForecast", "label_cn": "汇总预测"}],
            },
        ),
    ])
    return registry


class HarnessTest(unittest.TestCase):
    def test_harness_explains_only_structured_summary_and_anomaly_counts(self):
        summary = SummaryLine(
            scenario_id="BASELINE",
            budget_version="TEST-BUDGET",
            period=Month.parse("2026-08"),
            year=2026,
            company="TEST-COMPANY",
            department="测试单位",
            cost_center="TEST-CC",
            profit_center="TEST-PC",
            asset_category="TEST_CATEGORY",
            asset_source_type="PLANNED",
            event_type="ADDITION",
            depreciation_policy="POLICY-TEST",
            monthly_depreciation_sum=Decimal("79166.67"),
            addition_depreciation_impact=Decimal("79166.67"),
            disposal_depreciation_impact=Decimal("0.00"),
            impairment_depreciation_impact=Decimal("0.00"),
        )

        explanation = ExplanationHarness().explain_variance(
            question="Why did depreciation increase?",
            summaries=[summary],
            anomalies=[],
        )

        self.assertIn("79166.67", explanation)
        self.assertIn("ADDITION", explanation)
        self.assertIn("0 validation anomalies", explanation)

    def test_controlled_harness_uses_registered_read_only_tools(self):
        result = ControlledExplanationHarness(make_controlled_registry()).explain(
            scenario_id="BASELINE",
            department=None,
            year=None,
            style="finance",
            provider=EchoProvider(),
        )

        self.assertEqual(result["explanation"]["summary"], "5 tools, 1 actions")
        self.assertEqual(result["context"]["policy_context"]["depreciation_policy_label_cn"], "测试折旧政策")
        self.assertEqual(len(result["harness"]["tool_trace"]), 5)
        self.assertIn("Harness 只调用注册过的只读工具。", result["harness"]["guardrails"])

    def test_guardrail_rejects_unknown_or_mutating_tools(self):
        registry = ToolRegistry()
        registry.register(
            HarnessTool(
                name="unsafeWrite",
                label_cn="写入",
                description_cn="mutating",
                read_only=False,
                runner=lambda _args: None,
            )
        )
        harness = ControlledExplanationHarness(registry)

        with self.assertRaises(ValueError):
            harness.guardrails.validate_plan([
                ToolInvocation("missingTool", "缺失", {}, "missing")
            ])
        with self.assertRaises(ValueError):
            harness.guardrails.validate_plan([
                ToolInvocation("unsafeWrite", "写入", {}, "unsafe")
            ])
